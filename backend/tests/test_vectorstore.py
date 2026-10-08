"""Chroma nativo persistente + Gemini/OCI explícitamente simulados (#17)."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.rebuild import ManifiestoDocumento, reconstruir_demo, reconstruir_indice, reconstruir_workspace
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, IndiceInconsistenteError, VectorStoreChroma
from app.jobs.manager import (
    ContextoEjecucion,
    CuotaAgotadaError,
    CuotasModelo,
    CuotasProveedor,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.schemas.enums import DocumentStatus
from app.schemas.internal import Chunk
from app.storage.object_keys import clave_demo, clave_documento, clave_original, clave_workspace
from app.storage.provider import StorageConflict, StorageUnavailable
from doubles.gemini import DobleGemini

pytestmark = pytest.mark.integration_mock
MODELO = "gemini-embedding-2"


def ctx(workspace="espacio-a", cuotas=None):
    return ContextoEjecucion(
        "trabajo",
        workspace,
        "indexacion",
        time.monotonic() + 300,
        cuotas=cuotas or CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
        base_backoff_segundos=0,
    )


def chunks(documento="doc-a", workspace="espacio-a", texto="Las redes VCN conectan subredes privadas.", cantidad=2):
    return [
        Chunk(
            chunk_id=f"{workspace}-{documento}-{i}",
            document_id=documento,
            workspace_id=workspace,
            document_hash=hashlib.sha256(texto.encode()).hexdigest(),
            indice=i,
            texto=f"{texto} Sección {i}.",
            cantidad_tokens=20,
            source_name="redes.md",
            source_type="md",
            seccion="Redes",
            linea_inicio=i * 3 + 1,
            linea_fin=i * 3 + 3,
            tokenizer="cl100k_base",
            language="es",
        )
        for i in range(cantidad)
    ]


@pytest.fixture
def pila(tmp_path):
    doble = DobleGemini(dimension_embeddings=16)
    embeddings = GeminiEmbeddings(doble, modelo=MODELO, dimensiones=16)
    return VectorStoreChroma(tmp_path / "chroma", embeddings, tamano_lote=1), doble


def test_aislamiento_documentos_espacios_y_trazabilidad(pila):
    indice, doble = pila
    for workspace, documento in [("espacio-a", "doc-a"), ("espacio-a", "doc-b"), ("espacio-b", "doc-c")]:
        indice.indexar(workspace, documento, chunks(documento, workspace), contexto=ctx(workspace))
    encontrados = indice.buscar("espacio-a", "doc-a", "subredes", contexto=ctx())
    assert {hit.chunk.chunk_id for hit in encontrados} == {c.chunk_id for c in chunks()}
    assert all(hit.chunk.seccion == "Redes" and hit.chunk.linea_inicio for hit in encontrados)
    assert all(len(hit.vector) == 16 and 0 <= hit.similitud <= 1 for hit in encontrados)
    llamadas = doble.llamadas_embeddings
    assert indice.buscar("espacio-a", "doc-c", "subredes", contexto=ctx()) == []
    assert doble.llamadas_embeddings == llamadas
    with pytest.raises(PermissionError):
        indice.buscar("espacio-b", "doc-c", "subredes", contexto=ctx())


def test_persistencia_y_colecciones_por_modelo_dimension(pila):
    indice, doble = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    otro = VectorStoreChroma(indice.ruta, indice.embeddings)
    assert otro.contar("espacio-a", "doc-a", contexto=ctx()) == 2
    assert len(otro.buscar("espacio-a", "doc-a", "redes", contexto=ctx())) == 2
    distinto = VectorStoreChroma(indice.ruta, GeminiEmbeddings(DobleGemini(8), modelo=MODELO, dimensiones=8))
    assert distinto._privada.name != indice._privada.name
    assert distinto.contar("espacio-a", "doc-a", contexto=ctx()) == 0
    assert doble.llamadas_embeddings == 3


def test_rechaza_coleccion_con_preparacion_incompatible(pila):
    indice, _ = pila
    indice._privada.modify(metadata={"preparacion": "vieja"})
    with pytest.raises(IndiceInconsistenteError):
        VectorStoreChroma(indice.ruta, indice.embeddings)


def test_reutiliza_hash_en_mismo_espacio_sin_cruzarlo(pila):
    indice, doble = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    assert doble.llamadas_embeddings == 2
    assert indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx()).reutilizado
    assert indice.indexar("espacio-a", "doc-b", chunks("doc-b"), contexto=ctx()).reutilizado
    assert doble.llamadas_embeddings == 2
    assert {h.chunk.document_id for h in indice.buscar("espacio-a", "doc-b", "redes", contexto=ctx())} == {"doc-b"}
    indice.indexar("espacio-b", "doc-c", chunks("doc-c", "espacio-b"), contexto=ctx("espacio-b"))
    assert doble.llamadas_embeddings == 5
    # Cambiar un texto aun manteniendo el hash impide usar vectores ajenos.
    modificados = chunks("doc-d")
    modificados[0] = modificados[0].model_copy(update={"texto": "Otra información"})
    assert not indice.indexar("espacio-a", "doc-d", modificados, contexto=ctx()).reutilizado
    assert doble.llamadas_embeddings == 7


def test_borrado_no_reaparece_tras_reinicio_y_no_afecta_otro_doc(pila):
    indice, _ = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    indice.indexar("espacio-a", "doc-b", chunks("doc-b"), contexto=ctx())
    indice.borrar("espacio-a", "doc-a", contexto=ctx())
    assert indice._privada.get(where={"$and": [{"workspace_id": "espacio-a"}, {"document_id": "doc-a"}]})["ids"] == []
    otro = VectorStoreChroma(indice.ruta, indice.embeddings)
    assert otro.contar("espacio-a", "doc-a", contexto=ctx()) == 0
    assert otro.contar("espacio-a", "doc-b", contexto=ctx()) == 2
    with pytest.raises(TrabajoCanceladoError):
        otro.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())


def test_borrado_fisico_falla_pero_lapida_bloquea_lectura(pila, monkeypatch):
    indice, _ = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())

    def fallar(*args, **kwargs):
        raise RuntimeError("disco no disponible")

    monkeypatch.setattr(type(indice._privada), "delete", fallar)
    with pytest.raises(RuntimeError):
        indice.borrar("espacio-a", "doc-a", contexto=ctx())
    assert indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx()) == []


def test_fallo_segundo_lote_conserva_version_anterior(pila):
    indice, doble = pila
    indice.indexar("espacio-a", "doc-a", chunks(cantidad=1), contexto=ctx())
    original = doble.embed_sync

    def fallar(textos, *, timeout):
        if textos[0].endswith("Sección 1."):
            raise ValueError("respuesta inválida")
        return original(textos, timeout=timeout)

    doble.embed_sync = fallar
    with pytest.raises(ValueError):
        indice.indexar("espacio-a", "doc-a", chunks(texto="Nueva versión de las redes."), contexto=ctx())
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 1
    assert indice._privada.count() == 1


def test_cancelacion_en_llamada_y_borrado_en_vuelo_no_publican(pila):
    indice, doble = pila
    contexto = ctx()
    original = doble.embed_sync

    def cancelar(textos, *, timeout):
        vector = original(textos, timeout=timeout)
        contexto._evento_cancelacion.set()
        return vector

    doble.embed_sync = cancelar
    with pytest.raises(TrabajoCanceladoError):
        indice.indexar("espacio-a", "doc-a", chunks(), contexto=contexto)
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 0

    def borrar(textos, *, timeout):
        indice.borrar("espacio-a", "doc-b", contexto=ctx())
        return original(textos, timeout=timeout)

    doble.embed_sync = borrar
    with pytest.raises(TrabajoCanceladoError):
        indice.indexar("espacio-a", "doc-b", chunks("doc-b"), contexto=ctx())
    assert indice.contar("espacio-a", "doc-b", contexto=ctx()) == 0


def test_reintentos_y_cuotas_solo_por_contexto(pila):
    indice, doble = pila
    cuotas = CuotasProveedor({MODELO: CuotasModelo(10, 100000, 10)})
    doble.programar_error_embeddings(ReintentableError("temporal"), ReintentableError("temporal"))
    indice.indexar("espacio-a", "doc-a", chunks(cantidad=1), contexto=ctx(cuotas=cuotas))
    assert doble.llamadas_embeddings == 3
    assert cuotas.disponibles_hoy(MODELO) == 7
    indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx(cuotas=cuotas))
    assert cuotas.disponibles_hoy(MODELO) == 6


def test_cuota_agotada_no_publica_lotes_parciales(pila):
    indice, doble = pila
    cuotas = CuotasProveedor({MODELO: CuotasModelo(10, 100000, 1)})
    with pytest.raises(CuotaAgotadaError):
        indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx(cuotas=cuotas))
    assert doble.llamadas_embeddings == 1
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 0
    assert indice._privada.count() == 0


@pytest.mark.parametrize("cambio", [{"workspace_id": "otro"}, {"document_id": "otro"}, {"document_hash": None}])
def test_rechaza_chunks_ajenos_antes_de_gemini(pila, cambio):
    indice, doble = pila
    with pytest.raises(ValueError):
        indice.indexar("espacio-a", "doc-a", [chunks()[0].model_copy(update=cambio)], contexto=ctx())
    assert doble.llamadas_embeddings == 0


def guardar_fuente(storage, documento="doc-a", workspace="espacio-a", *, borrado=False, contenido=None):
    contenido = contenido or ("# Redes\n\nLas redes privadas conectan servicios de forma segura. " * 40).encode()
    manifest = ManifiestoDocumento(
        workspace_id=workspace,
        document_id=documento,
        source_name="redes.md",
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
        borrado=borrado,
    )
    if workspace == DEMO_WORKSPACE_ID:
        original, clave = clave_demo(f"{documento}/original"), clave_demo(f"{documento}/manifest.json")
    else:
        storage.upload(
            clave_workspace(workspace),
            json.dumps(
                {"workspace_id": workspace, "expira_en": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
            ),
            content_type="application/json",
        )
        original, clave = clave_original(workspace, documento), clave_documento(workspace, documento)
    storage.upload(original, contenido, content_type="text/markdown")
    storage.upload(clave, manifest.model_dump_json(), content_type="application/json")
    return manifest


def test_reconstruccion_completa_paginada_desde_originales(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage)
    guardar_fuente(mock_storage, "doc-b")
    guardar_fuente(mock_storage, "doc-ajeno", "espacio-b")
    cuotas = CuotasProveedor({MODELO: CuotasModelo(100, 100000, 100)})
    resultado = reconstruir_workspace(mock_storage, indice, contexto=ctx(cuotas=cuotas), tamano_pagina=1)
    assert {r.document_id for r in resultado} == {"doc-a", "doc-b"}
    assert all(r.estado == DocumentStatus.READY and not r.requiere_vision for r in resultado)
    assert doble.llamadas_embeddings > 0
    assert cuotas.disponibles_hoy(MODELO) == 100 - doble.llamadas_embeddings
    assert indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx())
    assert indice.contar("espacio-b", "doc-ajeno", contexto=ctx("espacio-b")) == 0


def test_reconstruccion_verifica_hash_y_propaga_fallo_oci(pila, mock_storage, monkeypatch):
    indice, doble = pila
    guardar_fuente(mock_storage)
    mock_storage.upload(clave_original("espacio-a", "doc-a"), b"alterado", content_type="text/plain")
    with pytest.raises(ValueError, match="SHA-256"):
        reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert doble.llamadas_embeddings == 0

    def caido(*args, **kwargs):
        raise StorageUnavailable("OCI caído")

    monkeypatch.setattr(mock_storage, "get", caido)
    with pytest.raises(StorageUnavailable):
        reconstruir_workspace(mock_storage, indice, contexto=ctx())


def test_manifiesto_borrado_impide_resurreccion_incluso_indice_nuevo(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage, borrado=True)
    assert reconstruir_workspace(mock_storage, indice, contexto=ctx()) == []
    assert indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx()) == []
    assert doble.llamadas_embeddings == 0


def test_cambio_manifiesto_durante_embeddings_no_publica(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage)
    original = doble.embed_sync

    def retirar(textos, *, timeout):
        guardar_fuente(mock_storage, borrado=True)
        return original(textos, timeout=timeout)

    doble.embed_sync = retirar
    with pytest.raises(StorageConflict):
        reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 0


def test_workspace_expirado_no_se_reconstruye(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage)
    mock_storage.upload(
        clave_workspace("espacio-a"),
        json.dumps(
            {"workspace_id": "espacio-a", "expira_en": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
        ),
        content_type="application/json",
    )
    with pytest.raises(TrabajoCanceladoError):
        reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert doble.llamadas_embeddings == 0


def test_demo_separada_solo_lectura_y_cuotas_reales_del_contexto(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage, "publico", DEMO_WORKSPACE_ID)
    cuotas = CuotasProveedor({MODELO: CuotasModelo(100, 100000, 100)})
    resultado = reconstruir_indice(mock_storage, indice, contextos=[], contexto_demo=ctx(DEMO_WORKSPACE_ID, cuotas))
    assert resultado[0].document_id == "publico"
    assert indice._privada.count() == 0
    assert cuotas.disponibles_hoy(MODELO) == 100 - doble.llamadas_embeddings
    assert indice.buscar_demo("publico", "redes", contexto=ctx())
    assert indice.buscar("espacio-a", "publico", "redes", contexto=ctx()) == []
    with pytest.raises(PermissionError):
        indice.borrar(DEMO_WORKSPACE_ID, "publico", contexto=ctx(DEMO_WORKSPACE_ID))
    with pytest.raises(PermissionError):
        indice.indexar(
            DEMO_WORKSPACE_ID, "publico", chunks("publico", DEMO_WORKSPACE_ID), contexto=ctx(DEMO_WORKSPACE_ID)
        )
    with pytest.raises(PermissionError):
        reconstruir_demo(mock_storage, indice, contexto=ctx())


def test_reindexacion_actualiza_ids_y_etiquetas_sin_repetir_embeddings(pila):
    indice, doble = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    nuevos = [c.model_copy(update={"chunk_id": c.chunk_id + "-v2", "source_name": "nuevo.md"}) for c in chunks()]
    assert indice.indexar("espacio-a", "doc-a", nuevos, contexto=ctx()).reutilizado
    assert doble.llamadas_embeddings == 2
    encontrados = indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx())
    assert {h.chunk.chunk_id for h in encontrados} == {c.chunk_id for c in nuevos}
    assert all(h.chunk.source_name == "nuevo.md" for h in encontrados)
    assert indice._privada.count() == 2


def test_borrar_elimina_tambien_chunks_en_otra_dimension(pila):
    indice, _ = pila
    otro = VectorStoreChroma(indice.ruta, GeminiEmbeddings(DobleGemini(8), modelo=MODELO, dimensiones=8))
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    otro.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    indice.borrar("espacio-a", "doc-a", contexto=ctx())
    assert indice._privada.count() == otro._privada.count() == 0


def test_indice_incompleto_requiere_reconstruccion(pila):
    indice, doble = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    indice._privada.delete(ids=indice._privada.get()["ids"][:1])
    with pytest.raises(IndiceInconsistenteError):
        indice.buscar("espacio-a", "doc-a", "redes", contexto=ctx())
    assert doble.llamadas_embeddings == 2


def test_reconstruccion_retira_indice_cuyo_manifiesto_ya_no_existe(pila, mock_storage):
    indice, _ = pila
    guardar_fuente(mock_storage)
    reconstruir_workspace(mock_storage, indice, contexto=ctx())
    mock_storage.delete(clave_documento("espacio-a", "doc-a"))
    assert reconstruir_workspace(mock_storage, indice, contexto=ctx()) == []
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 0
    assert indice._privada.count() == 0


def test_reconstruccion_completa_varios_espacios_y_demo(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage)
    guardar_fuente(mock_storage, "privado-b", "espacio-b")
    guardar_fuente(mock_storage, "publico", DEMO_WORKSPACE_ID)
    cuotas = CuotasProveedor({MODELO: CuotasModelo(100, 100000, 100)})
    reconstruir_indice(
        mock_storage,
        indice,
        contextos=[ctx("espacio-a", cuotas), ctx("espacio-b", cuotas)],
        contexto_demo=ctx(DEMO_WORKSPACE_ID, cuotas),
    )
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) > 0
    assert indice.contar("espacio-b", "privado-b", contexto=ctx("espacio-b")) > 0
    assert indice.buscar_demo("publico", "redes", contexto=ctx())
    assert indice.buscar("espacio-a", "privado-b", "redes", contexto=ctx()) == []


def test_reconstruccion_pdf_visual_sigue_processing(pila, mock_storage, documento_demo_vcn):
    indice, _ = pila
    manifest = guardar_fuente(mock_storage, contenido=documento_demo_vcn.read_bytes())
    manifest.source_name = "redes_vcn_oci.pdf"
    manifest.estado = DocumentStatus.READY  # Estado viejo no sustituye la revisión visual.
    mock_storage.upload(
        clave_documento("espacio-a", "doc-a"), manifest.model_dump_json(), content_type="application/json"
    )
    resultado = reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert resultado[0].requiere_vision
    assert resultado[0].estado == DocumentStatus.PROCESSING


def test_reconstruccion_escaneo_sin_texto_espera_vision(pila, mock_storage, tmp_path):
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "escaneo.pdf"
    documento = canvas.Canvas(str(pdf))
    documento.rect(40, 40, 100, 100, fill=1)
    documento.showPage()
    documento.save()
    indice, doble = pila
    manifest = guardar_fuente(mock_storage, contenido=pdf.read_bytes())
    manifest.source_name = "escaneo.pdf"
    mock_storage.upload(
        clave_documento("espacio-a", "doc-a"), manifest.model_dump_json(), content_type="application/json"
    )
    resultado = reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert resultado[0].requiere_vision and resultado[0].cantidad_chunks == 0
    assert resultado[0].estado == DocumentStatus.PROCESSING
    assert doble.llamadas_embeddings == 0


def test_persistencia_consultable_en_otro_proceso(pila):
    import os
    import subprocess
    import sys
    from pathlib import Path

    indice, _ = pila
    indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    script = """
import sys, time
sys.path[:0] = ['backend', 'backend/tests']
from app.core.rag.vectorstore import VectorStoreChroma
from app.core.rag.embeddings import GeminiEmbeddings
from app.jobs.manager import ContextoEjecucion, CuotasProveedor, CuotasModelo
from doubles.gemini import DobleGemini
modelo='gemini-embedding-2'
indice=VectorStoreChroma(sys.argv[1], GeminiEmbeddings(DobleGemini(16), modelo=modelo, dimensiones=16))
ctx=ContextoEjecucion('job', 'espacio-a', 'lectura', time.monotonic()+60,
                     cuotas=CuotasProveedor({modelo: CuotasModelo()}))
print(len(indice.buscar('espacio-a', 'doc-a', 'redes', contexto=ctx)))
"""
    resultado = subprocess.run(
        [sys.executable, "-B", "-c", script, str(indice.ruta)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
        env={**os.environ, "MOCK_GEMINI": "1", "MOCK_OCI": "1"},
    )
    assert resultado.stdout.strip() == "2"


def test_reconstruccion_repara_chunks_perdidos(pila, mock_storage):
    indice, doble = pila
    guardar_fuente(mock_storage)
    reconstruir_workspace(mock_storage, indice, contexto=ctx())
    cantidad = indice.contar("espacio-a", "doc-a", contexto=ctx())
    llamadas = doble.llamadas_embeddings
    indice._privada.delete(ids=indice._privada.get()["ids"][:1])
    reconstruido = reconstruir_workspace(mock_storage, indice, contexto=ctx())
    assert not reconstruido[0].reutilizado
    assert doble.llamadas_embeddings > llamadas
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == cantidad


def test_fallo_limpieza_no_oculta_error_original(pila, monkeypatch):
    indice, doble = pila
    doble.programar_error_embeddings(ValueError("embedding inválido"))

    def fallar(*args, **kwargs):
        raise RuntimeError("limpieza fallida")

    monkeypatch.setattr(type(indice._privada), "delete", fallar)
    with pytest.raises(ValueError, match="embedding inválido") as error:
        indice.indexar("espacio-a", "doc-a", chunks(), contexto=ctx())
    assert "invisibles" in error.value.__notes__[0]
    assert indice.contar("espacio-a", "doc-a", contexto=ctx()) == 0
