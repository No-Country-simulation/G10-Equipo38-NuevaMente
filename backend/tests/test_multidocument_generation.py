"""Tests de integración para generación multidocumental (§5 del plan de implementación).

Verifica esquemas, admisión, resolución de 'espacio_completo', límites de fuentes,
persistencia en generation_documents/job_documents, snapshot de fuentes,
ejecución del pipeline y desautorización ante retirada de fuentes secundarias.
"""

import json
import time
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.core.agents.prompts import cargar_plantilla, ejemplo_few_shot
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.main import crear_app
from app.schemas.pedagogical import AlcanceSolicitud
from app.schemas.requests import GenerateRequest
from app.schemas.responses import DocumentoFuente
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_critic import rubrica

pytestmark = pytest.mark.integration_mock


class DobleMultiDocumento(DobleGemini):
    def generar_sync(self, prompt, **kwargs):
        datos = json.loads(prompt)
        p = datos["parametros"]
        evidencias = datos.get("evidencia", [])
        chunk_primario = evidencias[0]["chunk"] if evidencias else {}
        solicitud = GenerateRequest.model_validate(p)
        salida = ejemplo_few_shot(solicitud.perfil_destinatario, solicitud.formato_salida, solicitud.idioma_salida)
        salida = json.loads(
            json.dumps(salida)
            .replace("[SOURCE_CHUNK_ID]", chunk_primario.get("chunk_id", "ch_1"))
            .replace("[SOURCE_SECTION]", chunk_primario.get("seccion") or "Fuente")
        )
        salida["metadatos"].update(
            idioma_origen=datos.get("idioma_origen", "es"),
            nicho_sector=p["nicho_sector"],
            nivel_detalle=p["nivel_detalle"],
            alcance={**p["alcance"], "secciones_cubiertas": datos.get("secciones_cubiertas", [])},
        )

        # Distribuir citas entre los chunks disponibles si hay más de una fuente
        chunks_disponibles = [e["chunk"] for e in evidencias] if evidencias else [chunk_primario]

        def citas(valor):
            if isinstance(valor, dict):
                for clave, item in valor.items():
                    if clave == "referencias":
                        nuevas = []
                        for i, ref in enumerate(item):
                            target = chunks_disponibles[i % len(chunks_disponibles)]
                            ref_dict = {
                                "chunk_id": target["chunk_id"],
                                "pagina": target.get("pagina"),
                                "seccion": target.get("seccion"),
                            }
                            if len(solicitud.document_ids) > 1:
                                ref_dict["document_id"] = target.get("document_id")
                            nuevas.append(ref_dict)
                        valor[clave] = nuevas
                    else:
                        citas(item)
            elif isinstance(valor, list):
                for item in valor:
                    citas(item)

        citas(salida)
        self.programar_generacion(json.dumps(salida))
        return super().generar_sync(prompt, **kwargs)

    def _revisar(self, **entrada):
        datos = json.loads(entrada["prompt"])
        fase = datos["tarea"]
        if fase == "descomponer":
            salida = {
                "afirmaciones": [
                    {"texto": "Afirmación sintética del doble", "ubicacion": ruta}
                    for ruta in list(datos["segmentos"])[:2]
                ]
            }
        elif fase == "juzgar":
            salida = {
                "juicios": [
                    {
                        "id_afirmacion": a["id"],
                        "estado": "respaldada",
                        "motivo": "Juicio simulado",
                        "referencias": a["citas_permitidas"],
                    }
                    for a in datos["afirmaciones"]
                ]
            }
        elif fase == "pedagogia":
            salida = {**rubrica(), "claridad_pedagogica": "alta"}
        elif fase == "quiz":
            refs = [e["chunk"]["chunk_id"] for e in datos["evidencia"]]
            salida = {
                "opciones": [
                    {
                        "pregunta_id": p["id"],
                        "option_id": o["option_id"],
                        "defendible": o["option_id"] == p["correct_option_id"],
                        "refutada_por_explicacion": o["option_id"] != p["correct_option_id"],
                        "motivo": "Juicio simulado",
                        "referencias": refs,
                    }
                    for p in datos["preguntas"]
                    for o in p["opciones"]
                ]
            }
        else:
            salida = {"chunk_id": datos["chunk_visual"], "estado": "aprobada", "descripcion": "Juicio simulado"}
        self.programar_revision(json.dumps(salida))
        return super()._revisar(**entrada)


@pytest.fixture
def entorno_multi(tmp_path, aislar_estado_seguridad):
    cfg = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleMultiDocumento(16)
    cuotas = CuotasProveedor(
        {
            m: CuotasModelo(1000, 10_000_000, 1000)
            for m in (cfg.gemini_embedding_model, cfg.gemini_generation_model, cfg.gemini_verification_model)
        }
    )
    app = crear_app(
        cfg,
        proveedor_embeddings=doble,
        proveedor_generacion=doble,
        proveedor_verificacion=doble,
        cuotas_proveedor=cuotas,
    )
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        e = SimpleNamespace(
            app=app,
            cliente=cliente,
            ws=ws,
            db=app.state.db,
            gestor=app.state.jobs_manager,
            storage=app.state.storage_provider,
            doble=doble,
            headers={"Authorization": "Bearer " + ws["token"]},
            docs=[],
        )

        def subir(titulo, contenido):
            subida = cliente.post(
                "/api/documents/upload",
                headers=e.headers,
                json={"documento_titulo": titulo, "documento_contenido": contenido * 20},
            )
            assert subida.status_code == 202, subida.text
            recibo = subida.json()
            esperar_estado(e, recibo)
            doc_id = recibo["document_id"]
            e.docs.append(doc_id)
            return doc_id

        e.subir = subir
        yield e


def esperar_estado(e, recibo, status="completed", *, timeout=60):
    limite = time.monotonic() + timeout
    ultimo = None
    while time.monotonic() < limite:
        respuesta = e.cliente.get(recibo["status_url"], headers=e.headers)
        assert respuesta.status_code == 200, respuesta.text
        ultimo = respuesta.json()
        if ultimo["status"] == status:
            return ultimo
        if ultimo["status"] not in {"queued", "running"}:
            fila = e.db.obtener_generacion(recibo["generation_id"])
            job = e.db.job_generacion(recibo["generation_id"])
            pytest.fail(f"Estado terminal no esperado: {ultimo}; fila={fila}; job={job}")
        time.sleep(0.02)
    pytest.fail(f"Timeout esperando {status}: {ultimo}")


def test_solicitud_multidocumento_orden_determinista_y_scope_hash():
    # Permutaciones de document_ids deben normalizarse al mismo orden determinista
    req_ab = GenerateRequest(
        document_ids=["doc_b", "doc_a"],
        perfil_destinatario="principiante",
        formato_salida="flashcards",
        nicho_sector="general",
        nivel_detalle="didactico",
        idioma_salida="es",
    )
    req_ba = GenerateRequest(
        document_ids=["doc_a", "doc_b"],
        perfil_destinatario="principiante",
        formato_salida="flashcards",
        nicho_sector="general",
        nivel_detalle="didactico",
        idioma_salida="es",
    )
    assert req_ab.document_ids == ["doc_a", "doc_b"]
    assert req_ba.document_ids == ["doc_a", "doc_b"]
    assert req_ab.document_id == "doc_a"
    assert req_ab.parametros_persistidos() == req_ba.parametros_persistidos()


def test_limite_maximo_cinco_documentos_en_solicitud(entorno_multi):
    e = entorno_multi
    # Enviar 6 document_ids debe fallar en validación (422 o 400)
    seis_docs = [f"doc_{i}" for i in range(6)]
    res = e.cliente.post(
        "/api/generate",
        json={
            "document_ids": seis_docs,
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        },
        headers=e.headers,
    )
    assert res.status_code in {400, 422}


def test_espacio_completo_resolucion_y_rechazo_por_exceso(entorno_multi):
    e = entorno_multi
    doc1 = e.subir("Fuente 1", "Contenido sobre redes y servidores.")
    doc2 = e.subir("Fuente 2", "Contenido sobre almacenamiento y bases de datos.")

    # Con 2 documentos listos, espacio_completo debe admitir y resolver ambos
    res = e.cliente.post(
        "/api/generate",
        json={
            "document_id": doc1,
            "alcance": {"tipo": "espacio_completo"},
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        },
        headers=e.headers,
    )
    assert res.status_code == 202, res.text
    recibo = res.json()
    salida = esperar_estado(e, recibo)
    assert salida["status"] == "completed"

    # Verificar que generation_documents y job_documents contienen ambos documentos
    gen_id = recibo["generation_id"]
    docs_vinculados = e.db.documentos_generacion(gen_id)
    assert set(docs_vinculados) == {doc1, doc2}

    # Verificar snapshot_fuentes_json
    fila_gen = e.db.obtener_generacion(gen_id)
    assert fila_gen["snapshot_fuentes_json"] is not None
    snapshot = json.loads(fila_gen["snapshot_fuentes_json"])
    assert len(snapshot) == 2
    assert {s["document_id"] for s in snapshot} == {doc1, doc2}


def test_generacion_multidocumental_completa_y_documentos_fuente(entorno_multi, tmp_path):
    e = entorno_multi
    doc1 = e.subir("Arquitectura A", "Conceptos de arquitectura de sistemas distribuidos.")
    doc2 = e.subir("Arquitectura B", "Patrones de microservicios y mensajería asíncrona.")

    res = e.cliente.post(
        "/api/generate",
        json={
            "document_ids": [doc1, doc2],
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        },
        headers=e.headers,
    )
    assert res.status_code == 202, res.text
    recibo = res.json()
    salida = esperar_estado(e, recibo)
    assert salida["status"] == "completed"

    contenido = salida["contenido"]
    assert "documentos_fuente" in contenido
    assert len(contenido["documentos_fuente"]) == 2
    assert {df["document_id"] for df in contenido["documentos_fuente"]} == {doc1, doc2}

    # Verificar que las citas del contenido incluyen document_id
    items = contenido["contenido_adaptado"]["items"]
    encontrada_con_doc = False
    for item in items:
        for ref in item.get("referencias", []):
            if ref.get("document_id") in {doc1, doc2}:
                encontrada_con_doc = True
    assert encontrada_con_doc, "Debe haber citas con document_id identificando la procedencia"

    # Serializar el paquete completo como lo haría la evidencia D (§8.2), pero
    # SIEMPRE en tmp_path: un test jamás escribe archivos versionados del repo.
    # La evidencia de un escenario D se produce con proveedores reales fuera de
    # la suite; regenerarla aquí la volvería no determinista (IDs aleatorios) y
    # presentaría salida de dobles como ejecución real.
    out_file = tmp_path / "salida-multidocumental-d.json"
    json_bytes = json.dumps(contenido, indent=2, ensure_ascii=False).encode("utf-8")
    out_file.write_bytes(json_bytes)
    assert out_file.is_file()
    assert len(json_bytes) > 100


def test_retirada_de_fuente_secundaria_invalida_generacion_multidocumental(entorno_multi):
    e = entorno_multi
    doc_primario = e.subir("Doc Primario", "Detalles de seguridad de redes y firewalls.")
    doc_secundario = e.subir("Doc Secundario", "Protocolos de autenticación OAuth y JWT.")

    res = e.cliente.post(
        "/api/generate",
        json={
            "document_ids": [doc_primario, doc_secundario],
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        },
        headers=e.headers,
    )
    assert res.status_code == 202, res.text
    recibo = res.json()
    salida = esperar_estado(e, recibo)
    assert salida["status"] == "completed"

    gen_id = recibo["generation_id"]
    # Antes del borrado, el endpoint de estado responde 200
    res_antes = e.cliente.get(f"/api/generations/{gen_id}", headers=e.headers)
    assert res_antes.status_code == 200

    # Borramos la fuente secundaria (doc_secundario)
    res_del = e.cliente.delete(f"/api/documents/{doc_secundario}", headers=e.headers)
    assert res_del.status_code in {202, 204}

    # Tras borrar la fuente secundaria, la generación ya NO debe servirse (404 NOT_FOUND)
    res_despues = e.cliente.get(f"/api/generations/{gen_id}", headers=e.headers)
    assert res_despues.status_code == 404


def test_solicitud_rechaza_mas_de_cinco_documentos_unitario():
    with pytest.raises(ValidationError, match="No se permiten más de 5 documentos"):
        GenerateRequest(
            document_ids=[f"doc_{i}" for i in range(6)],
            perfil_destinatario="principiante",
            formato_salida="flashcards",
            nicho_sector="general",
            nivel_detalle="didactico",
            idioma_salida="es",
        )
    with pytest.raises(ValidationError, match="No se permiten más de 5 documentos"):
        AlcanceSolicitud(
            tipo="espacio_completo",
            document_ids=[f"doc_{i}" for i in range(6)],
        )


def test_serializacion_compatibilidad_unidocumental_y_multidocumental():
    from test_schemas import salida_valida

    # 1 documento: documentos_fuente y alcance.document_ids se omiten
    contenido = {
        "tipo": "flashcards",
        "titulo": "Prueba",
        "items": [
            {
                "id": "item_1",
                "frente": "¿Qué es?",
                "dorso": "Es un concepto",
                "referencias": [{"chunk_id": "ch_1", "pagina": 1, "seccion": "Intro"}],
            }
        ],
    }
    pkg_uni = salida_valida(contenido)
    uni_dump = pkg_uni.model_dump(mode="json")
    assert "documentos_fuente" not in uni_dump
    assert "document_ids" not in uni_dump["metadatos"]["alcance"]

    # 2 documentos: documentos_fuente se conservan
    pkg_multi = pkg_uni.model_copy(
        update={
            "documentos_fuente": [
                pkg_uni.documento_fuente.model_copy(deep=True),
                DocumentoFuente(
                    document_id="doc_02HX",
                    titulo="Doc 2",
                    hash="sha256:def",
                    version="v1",
                ),
            ]
        }
    )
    multi_dump = pkg_multi.model_dump(mode="json")
    assert "documentos_fuente" in multi_dump
    assert len(multi_dump["documentos_fuente"]) == 2


def test_generacion_rechaza_documento_inexistente_inmediatamente(entorno_multi):
    e = entorno_multi
    doc1 = e.subir("Doc Existente", "Contenido disponible en el workspace.")
    res = e.cliente.post(
        "/api/generate",
        json={
            "document_ids": [doc1, "doc_fantasma_inexistente"],
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        },
        headers=e.headers,
    )
    assert res.status_code == 404
    cuerpo = res.json()
    assert cuerpo.get("error", {}).get("code") == "NOT_FOUND"
    assert "no encontrado" in cuerpo.get("error", {}).get("message", "").lower()


def test_prompts_multidocumento_instruccion_sintesis_sin_inventar_ids():
    solicitud = GenerateRequest(
        document_ids=["doc_a", "doc_b"],
        perfil_destinatario="principiante",
        formato_salida="flashcards",
        nicho_sector="general",
        nivel_detalle="didactico",
        idioma_salida="es",
    )
    plantilla = cargar_plantilla(solicitud)
    assert "Síntesis multidocumental obligatoria" in plantilla.writer
    assert "Citar fielmente los fragmentos de origen (chunk_id)" in plantilla.writer
    assert "No fabricar IDs de sistema" in plantilla.writer
