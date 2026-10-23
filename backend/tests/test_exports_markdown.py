"""#43: plantillas y caché con SQLite/storage reales, sin LLM ni Chroma."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
import test_quiz_progress as progreso
from app.exports.markdown import renderizar_markdown
from app.exports.router import ServicioExportaciones, validar_formato
from app.schemas.enums import JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import PedagogicalOutput
from app.storage.object_keys import clave_export, clave_output, clave_workspace
from app.storage.provider import StorageNotFound, StorageUnavailable
from test_schemas import GUION, QUIZ, TODOS_LOS_FORMATOS, TUTORIAL, salida_valida

pytestmark = pytest.mark.unit
espacio_exportaciones = progreso.estudio


@pytest.mark.parametrize("tipo", TODOS_LOS_FORMATOS)
@pytest.mark.parametrize(
    "idioma,objetivos,referencias",
    [
        ("es", "Objetivos de aprendizaje", "Referencias"),
        ("en", "Learning objectives", "References"),
        ("pt", "Objetivos de aprendizagem", "Referências"),
    ],
)
def test_plantillas_cinco_formatos_unicode_y_etiquetas_localizadas(tipo, idioma, objetivos, referencias):
    paquete = salida_valida(TODOS_LOS_FORMATOS[tipo][0]).model_dump(mode="json")
    paquete["metadatos"]["idioma_salida"] = idioma
    paquete["contenido_adaptado"]["titulo"] = "Árbol de redes: conexão, ação y arquitectura"
    modelo = PedagogicalOutput.model_validate(paquete)
    anterior = modelo.model_dump_json()
    datos = renderizar_markdown(modelo)
    md = datos.decode("utf-8")
    assert md.startswith("# Árbol de redes: conexão, ação y arquitectura\n")
    assert "## " + objetivos in md and "## " + referencias in md
    assert "[^1]" in md and "chk\\_001" in md and "Conceptos" in md
    assert "outputs/" not in md and "modelo_generacion" not in md
    assert modelo.model_dump_json() == anterior and renderizar_markdown(modelo) == datos


def test_quiz_separa_todas_las_preguntas_de_claves_y_justificaciones():
    contenido = deepcopy(QUIZ)
    contenido["preguntas"][0]["justificacion"] = "EXPLICACION_RESERVADA_UNO"
    contenido["preguntas"].append(
        {**deepcopy(contenido["preguntas"][0]), "id": "q_002", "justificacion": "EXPLICACION_RESERVADA_DOS"}
    )
    md = renderizar_markdown(salida_valida(contenido)).decode()
    preguntas, soluciones = md.split("## Soluciones", 1)
    assert preguntas.count("### Pregunta") == soluciones.count("### Pregunta") == 2
    assert "Respuesta correcta" not in preguntas and "EXPLICACION" not in preguntas
    assert "EXPLICACION\\_RESERVADA\\_UNO" in soluciones and "EXPLICACION\\_RESERVADA\\_DOS" in soluciones
    assert "**Respuesta correcta:** A: Un rango CIDR" in soluciones


def test_tutorial_conserva_codigo_literal_y_fence_no_puede_cerrarse_desde_el_snippet():
    contenido = deepcopy(TUTORIAL)
    codigo = 'print("ação")\n```\n<script>alert(1)</script>\n`````\n# salir del bloque'
    contenido["pasos"][0]["codigo"] = codigo
    md = renderizar_markdown(salida_valida(contenido)).decode()
    assert "\n``````\n" + codigo + "\n``````\n" in md
    assert "Resultado esperado" in md and "Verificación" in md and "Cuenta de OCI" in md


def test_textos_no_crean_html_activo_enlaces_imagenes_o_encabezados():
    contenido = deepcopy(GUION)
    contenido["titulo"] = "Título\n## Inyectado"
    contenido["escenas"][0]["narracion"] = "<script>alert(1)</script> ![x](https://externo.example/r)\n## Encabezado"
    md = renderizar_markdown(salida_valida(contenido)).decode()
    assert "<script>" not in md and "![x](" not in md and "\n## Encabezado" not in md
    assert "&lt;script&gt;" in md and "\\!\\[x\\]" in md and "# Título \\#\\# Inyectado" in md
    assert "## Escenas" in md and "Diapositiva" in md and "Pregunta interactiva" in md


@pytest.mark.parametrize("tipo", TODOS_LOS_FORMATOS)
def test_json_igual_al_objeto_pretty_original_y_md_cacheado(espacio_exportaciones, tipo, monkeypatch):
    e = espacio_exportaciones
    paquete = e.publicar("gen_export", TODOS_LOS_FORMATOS[tipo][0])
    original = (json.dumps(paquete.model_dump(mode="json"), ensure_ascii=False, indent=3) + "\n").encode()
    e.storage.upload(clave_output("ws_estudio", "gen_export"), original)
    servicio = ServicioExportaciones(e.catalogo)
    json_descarga = servicio.descargar(e.token, "gen_export", "json")
    md_descarga = servicio.descargar(e.token, "gen_export", "md")
    assert json_descarga.contenido == original and json_descarga.nombre == "gen_export.json"
    assert md_descarga.contenido == renderizar_markdown(paquete)
    assert e.storage.get(clave_export("ws_estudio", "gen_export", "json")) == original
    assert e.storage.get(clave_export("ws_estudio", "gen_export", "md")) == md_descarga.contenido
    assert e.token.encode() not in original + md_descarga.contenido

    def no_escribir(*args, **kwargs):
        pytest.fail("Una descarga cacheada no vuelve a escribir")

    monkeypatch.setattr(e.storage, "upload", no_escribir)
    assert servicio.descargar(e.token, "gen_export", "md") == md_descarga


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "rejected_quality"])
def test_solo_completed_antes_de_validar_compatibilidad(espacio_exportaciones, status):
    e = espacio_exportaciones
    e.db.actualizar_generacion(e.paquete.generation_id, JobStatus(status))
    with pytest.raises(ErrorAplicacion) as error:
        ServicioExportaciones(e.catalogo).descargar(e.token, e.paquete.generation_id, "csv")
    assert error.value.error.code == ErrorCode.INVALID_STATE
    assert not e.storage.list("exports/").items


@pytest.mark.parametrize("tipo", TODOS_LOS_FORMATOS)
@pytest.mark.parametrize("formato", ["json", "md"])
def test_compatibilidad_implementada(tipo, formato):
    validar_formato(tipo, formato)


@pytest.mark.parametrize("tipo", ["tutorial", "quiz", "resumen_ejecutivo", "guion_clase"])
@pytest.mark.parametrize("formato", ["csv", "tsv", "apkg"])
def test_anki_no_convierte_implicitamente_otros_formatos(tipo, formato):
    with pytest.raises(ErrorAplicacion) as error:
        validar_formato(tipo, formato)
    assert error.value.error.code == ErrorCode.EXPORT_INCOMPATIBLE


@pytest.mark.parametrize("formato", ["exe", "../md", "JSON", "csv", "tsv", "apkg"])
def test_exportadores_pendientes_y_nombres_no_admitidos_no_escriben(espacio_exportaciones, formato):
    e = espacio_exportaciones
    with pytest.raises(ErrorAplicacion) as error:
        ServicioExportaciones(e.catalogo).descargar(e.token, e.paquete.generation_id, formato)
    assert error.value.error.code == ErrorCode.EXPORT_INCOMPATIBLE
    assert not e.storage.list("exports/").items


def test_ownership_y_revocacion_remota_incluso_con_cache(espacio_exportaciones):
    e = espacio_exportaciones
    servicio = ServicioExportaciones(e.catalogo)
    servicio.descargar(e.token, e.paquete.generation_id, "md")
    e.db.crear_workspace("ws_otro", "codigo-otro", dias_validez=30)
    e.db.crear_sesion("token-otro", "ws_otro", horas_validez=24)
    with pytest.raises(ErrorAplicacion) as error:
        servicio.descargar("token-otro", e.paquete.generation_id, "md")
    assert error.value.error.code == ErrorCode.NOT_FOUND
    manifest = json.loads(e.storage.get(clave_workspace("ws_estudio")))
    manifest["recursos_borrados"] = [
        {
            "recurso_tipo": "generation",
            "recurso_id": e.paquete.generation_id,
            "solicitado_en": "2026-10-12T00:00:00Z",
            "purga_despues_en": "2026-11-12T00:00:00Z",
        }
    ]
    e.storage.upload(clave_workspace("ws_estudio"), json.dumps(manifest))
    with pytest.raises(ErrorAplicacion) as error:
        servicio.descargar(e.token, e.paquete.generation_id, "md")
    assert error.value.error.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize("formato", ["json", "md"])
def test_cache_alterada_no_se_entrega(espacio_exportaciones, formato):
    e = espacio_exportaciones
    e.storage.upload(clave_export("ws_estudio", e.paquete.generation_id, formato), b"contenido ajeno y secreto")
    with pytest.raises(StorageUnavailable):
        ServicioExportaciones(e.catalogo).descargar(e.token, e.paquete.generation_id, formato)


@pytest.mark.parametrize("campo", ["status", "titulo"])
def test_canonico_con_claves_duplicadas_no_es_una_fuente_unica(espacio_exportaciones, campo):
    e = espacio_exportaciones
    clave = clave_output("ws_estudio", e.paquete.generation_id)
    datos = e.storage.get(clave).decode()
    datos = datos.replace('"' + campo + '":', '"' + campo + '":"valor ambiguo", "' + campo + '":', 1)
    e.storage.upload(clave, datos)
    with pytest.raises(StorageUnavailable):
        ServicioExportaciones(e.catalogo).descargar(e.token, e.paquete.generation_id, "json")
    assert not e.storage.list("exports/").items


def test_solicitudes_concurrentes_y_reinicio_reutilizan_cache(espacio_exportaciones, monkeypatch):
    e = espacio_exportaciones
    servicio = ServicioExportaciones(e.catalogo)
    original, escrituras = e.storage.upload, []

    def guardar(clave, *args, **kwargs):
        resultado = original(clave, *args, **kwargs)
        if clave.startswith("exports/"):
            escrituras.append(clave)
        return resultado

    monkeypatch.setattr(e.storage, "upload", guardar)
    with ThreadPoolExecutor(2) as pool:
        a, b = [pool.submit(servicio.descargar, e.token, e.paquete.generation_id, "md") for _ in range(2)]
        assert a.result(5) == b.result(5)
    assert len(escrituras) == 1
    nuevo = ServicioExportaciones(e.catalogo)
    assert nuevo.descargar(e.token, e.paquete.generation_id, "md").contenido == a.result().contenido
    assert len(escrituras) == 1


def test_fallo_derivado_es_visible_y_reintento_conserva_canonico(espacio_exportaciones, monkeypatch):
    e = espacio_exportaciones
    servicio = ServicioExportaciones(e.catalogo)
    original, paquete = e.storage.upload, e.paquete
    canonico = e.storage.get(clave_output("ws_estudio", paquete.generation_id))

    def fallar(clave, *args, **kwargs):
        if clave.endswith("/md"):
            raise StorageUnavailable("fallo simulado")
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    with pytest.raises(StorageUnavailable):
        servicio.preparar("ws_estudio", paquete, canonico, lambda: None)
    with pytest.raises(StorageNotFound):
        e.storage.get(clave_export("ws_estudio", paquete.generation_id, "md"))
    monkeypatch.setattr(e.storage, "upload", original)
    servicio.preparar("ws_estudio", paquete, canonico, lambda: None)
    assert e.storage.get(clave_output("ws_estudio", paquete.generation_id)) == canonico
    assert servicio.descargar(e.token, paquete.generation_id, "md").contenido


def test_renderizar_markdown_multidocumento(espacio_exportaciones):
    e = espacio_exportaciones
    from app.schemas.pedagogical import Referencia
    from app.schemas.responses import DocumentoFuente

    p = e.paquete.model_copy(deep=True)
    f1 = DocumentoFuente(document_id="doc1", titulo="Doc 1", version="v1", hash="hash1", procedencia="p1")
    f2 = DocumentoFuente(document_id="doc2", titulo="Doc 2", version="v2", hash="hash2", procedencia="p2")
    p.documento_fuente = f1
    p.documentos_fuente = [f1, f2]
    refs = [
        Referencia(document_id="doc1", chunk_id="chk1", pagina=1, seccion="Sec A"),
        Referencia(document_id="doc2", chunk_id="chk2", pagina=2, seccion="Sec B"),
    ]
    if hasattr(p.contenido_adaptado, "items"):
        p.contenido_adaptado.items[0].referencias = refs
    elif hasattr(p.contenido_adaptado, "preguntas"):
        p.contenido_adaptado.preguntas[0].referencias = refs
    salida = renderizar_markdown(p).decode("utf-8")
    assert "Documentos fuente" in salida
    assert "Doc 1" in salida and "Doc 2" in salida
    assert "Documento: doc1" in salida
    assert "Documento: doc2" in salida
