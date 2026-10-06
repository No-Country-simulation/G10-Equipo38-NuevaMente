"""Writer con doble y SDK/HTTP simulado: formatos, citas, límites y fallos reales de contrato."""

import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import httpx
import pytest
from app.config import Configuracion
from app.core.agents.gemini_generation import ClienteGeminiGeneracion
from app.core.agents.graph_state import DependenciasGrafo, DocumentoGeneracion, EstadoGrafo
from app.core.agents.prompts import PROMPT_VERSION, ejemplo_few_shot
from app.core.agents.supervisor import crear_estado_inicial
from app.core.agents.writer import DependenciasWriter, writer
from app.jobs.manager import CuotasModelo, CuotasProveedor, ReintentableError
from app.schemas.enums import PedagogicalFormat
from app.schemas.internal import Chunk, EvidenciaRecuperada
from app.schemas.responses import DocumentoFuente, Trazabilidad
from google import genai
from google.genai import types
from pydantic import ValidationError

pytestmark = pytest.mark.unit
MODELO = "modelo-test"


@pytest.fixture
def entorno(doble_gemini):
    from app.jobs.manager import ContextoEjecucion

    ctx = ContextoEjecucion(
        "job_test",
        "ws_test",
        "generacion",
        time.monotonic() + 300,
        cuotas=CuotasProveedor({MODELO: CuotasModelo(rpm=100, tpm=1_000_000, rpd=100)}),
        base_backoff_segundos=0,
    )
    documento = DocumentoGeneracion(
        workspace_id=ctx.workspace_id,
        fuente=DocumentoFuente(document_id="doc_test", titulo="Fuente de pruebas", hash="sha256:test", version="v1"),
        estado="ready",
        idioma_origen="en",
        secciones=("sec_1",),
    )
    grafo = DependenciasGrafo(ctx, lambda ws, document_id: documento)
    config = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        gemini_generation_model=MODELO,
        generation_max_output_tokens=1000,
    )
    return DependenciasWriter(grafo, doble_gemini, config), documento


def estado_inicial(entorno, formato="flashcards", **parametros):
    estado = crear_estado_inicial(
        {
            "document_id": "doc_test",
            "perfil_destinatario": "principiante",
            "formato_salida": formato,
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "pt",
            **parametros,
        },
        generation_id="gen_test",
        dependencias=entorno[0].grafo,
    )
    estado.secciones_cubiertas = ["sec_1"]
    estado.evidencia = [
        EvidenciaRecuperada(
            chunk=Chunk(
                chunk_id="ch_1",
                document_id="doc_test",
                workspace_id="ws_test",
                document_hash="sha256:test",
                indice=0,
                texto="Texto sintético de la fuente.",
                cantidad_tokens=10,
                pagina=1,
                seccion="Fundamentos",
            ),
            score=0.9,
            consulta="Fundamentos",
        )
    ]
    estado.trazabilidad = Trazabilidad(
        modelo_generacion="anterior",
        modelo_verificacion="juez-test",
        modelo_embeddings="embedding-test",
        prompt_version="anterior",
        parser_version="3",
        retrieval={"k": 5, "fetch_k": 15, "lambda_mult": 0.7},
    )
    return estado


def respuesta(estado):
    parametros = estado.parametros
    datos = ejemplo_few_shot(parametros.perfil_destinatario, parametros.formato_salida, parametros.idioma_salida)
    datos = json.loads(
        json.dumps(datos).replace("[SOURCE_CHUNK_ID]", "ch_1").replace("[SOURCE_SECTION]", "Fundamentos")
    )
    datos["metadatos"].update(
        idioma_origen=estado.idioma_origen,
        nicho_sector=parametros.nicho_sector,
        nivel_detalle=parametros.nivel_detalle,
        alcance={**parametros.alcance.model_dump(), "secciones_cubiertas": estado.secciones_cubiertas},
    )
    return datos


def aplicar(estado, update):
    return EstadoGrafo.model_validate({**estado.model_dump(), **update})


@pytest.mark.parametrize("formato", PedagogicalFormat)
def test_cinco_formatos_tipos_citas_y_metadata(entorno, formato):
    estado = estado_inicial(entorno, formato)
    entorno[0].proveedor.programar_generacion(json.dumps(respuesta(estado)))
    antes = estado.model_dump_json()
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "running" and nuevo.error is None
    assert nuevo.borrador.contenido_adaptado.tipo == formato
    assert nuevo.borrador.metadatos.idioma_origen == "en"
    assert nuevo.borrador.metadatos.idioma_salida == "pt"
    assert nuevo.borrador.metadatos.conceptos_clave and nuevo.borrador.metadatos.objetivos_aprendizaje
    assert nuevo.referencias[0].model_dump() == {"chunk_id": "ch_1", "pagina": 1, "seccion": "Fundamentos"}
    assert nuevo.trazabilidad.prompt_version == PROMPT_VERSION
    assert nuevo.trazabilidad.modelo_generacion == MODELO
    assert nuevo.intento == nuevo.presupuesto.usadas == entorno[0].proveedor.llamadas_generacion == 1
    assert nuevo.evaluacion_pedagogica is None and nuevo.persistencia is None
    assert estado.model_dump_json() == antes
    assert EstadoGrafo.model_validate_json(nuevo.model_dump_json()) == nuevo


@pytest.mark.parametrize("problema", ["chunk", "pagina", "seccion", "perfil", "idioma", "cobertura", "alcance"])
def test_corrige_citas_y_metadata_antes_de_critic(entorno, problema):
    estado = estado_inicial(entorno)
    mala, buena = respuesta(estado), respuesta(estado)
    cita = mala["contenido_adaptado"]["items"][0]["referencias"][0]
    if problema == "chunk":
        cita["chunk_id"] = "inventado"
    elif problema == "pagina":
        cita["pagina"] = 999
    elif problema == "seccion":
        cita["seccion"] = "otra sección"
    elif problema == "perfil":
        mala["metadatos"]["perfil_aplicado"] = "ejecutivo"
    elif problema == "idioma":
        mala["metadatos"]["idioma_salida"] = "en"
    elif problema == "cobertura":
        mala["metadatos"]["alcance"]["secciones_cubiertas"] = ["seccion_inventada"]
    else:
        mala["metadatos"]["alcance"] = {"tipo": "seccion", "seccion_id": "sec_1"}
    proveedor = entorno[0].proveedor
    proveedor.programar_generacion(json.dumps(mala), json.dumps(buena))
    proveedor.generar_sync = Mock(wraps=proveedor.generar_sync)
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.borrador is not None and nuevo.error is None
    assert nuevo.intento == nuevo.presupuesto.usadas == proveedor.llamadas_generacion == 2
    correccion = json.loads(proveedor.generar_sync.call_args.args[0])
    assert correccion["feedback"]
    assert all(ref.chunk_id == "ch_1" for ref in nuevo.referencias)


@pytest.mark.parametrize("mala", ["texto Markdown suelto", "```json\n{}\n```", "{}", '{"x":1,"x":2}', '{"x":NaN}'])
def test_respuesta_malformada_no_expone_borrador(entorno, mala):
    estado = estado_inicial(entorno)
    entorno[0].proveedor.programar_generacion(mala, mala, mala)
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "failed" and nuevo.error.code == "VALIDATION_ERROR"
    assert nuevo.intento == nuevo.presupuesto.usadas == entorno[0].proveedor.llamadas_generacion == 3
    assert nuevo.borrador is None and not nuevo.referencias
    assert mala not in nuevo.model_dump_json()


def test_feedback_critic_no_reinicia_las_tres_redacciones(entorno):
    estado = estado_inicial(entorno)
    estado.intento = 2
    estado.feedback = ["Reescribir la explicación completa con las limitaciones de la fuente."]
    estado.presupuesto.usadas = 6
    proveedor = entorno[0].proveedor
    proveedor.generar_sync = Mock(wraps=proveedor.generar_sync)
    proveedor.programar_generacion(json.dumps(respuesta(estado)))
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.intento == 3 and nuevo.presupuesto.usadas == 7
    assert json.loads(proveedor.generar_sync.call_args.args[0])["feedback"] == estado.feedback
    terminal = aplicar(nuevo, writer(nuevo, entorno[0]))
    assert terminal.status == "rejected_quality" and terminal.borrador is None
    assert proveedor.llamadas_generacion == 1


def test_retry_tecnico_no_es_otra_redaccion(entorno):
    estado = estado_inicial(entorno)
    entorno[0].proveedor.programar_generacion(
        ReintentableError("fallo 1"), ReintentableError("fallo 2"), json.dumps(respuesta(estado))
    )
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.intento == 1 and nuevo.presupuesto.usadas == 3
    assert entorno[0].proveedor.llamadas_generacion == 3


def test_presupuesto_bloquea_retry_sin_reservar_cuota_adicional(entorno):
    estado = estado_inicial(entorno)
    estado.presupuesto.usadas = 19
    entorno[0].proveedor.programar_generacion(ReintentableError("transitorio"), "no llamar")
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "failed" and nuevo.error.code == "GENERATION_BUDGET"
    assert nuevo.presupuesto.usadas == 20 and nuevo.intento == 1
    assert entorno[0].proveedor.llamadas_generacion == 1
    assert entorno[0].grafo.ejecucion.cuotas.disponibles_hoy(MODELO) == 99


def test_cuota_denegada_no_consume_redaccion_ni_presupuesto(entorno):
    estado = estado_inicial(entorno)
    entorno[0].grafo.ejecucion.cuotas = CuotasProveedor({MODELO: CuotasModelo(rpd=1)})
    entorno[0].grafo.ejecucion.cuotas.permitir(MODELO)
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.error.code == "RATE_LIMITED" and nuevo.status == "failed"
    assert nuevo.intento == nuevo.presupuesto.usadas == entorno[0].proveedor.llamadas_generacion == 0


@pytest.mark.parametrize(
    "problema", ["sin_evidencia", "ajena", "hash", "sin_trazabilidad", "fuente", "cancelacion", "deadline"]
)
def test_entrada_y_runtime_invalidos_no_llaman_gemini(entorno, problema):
    estado = estado_inicial(entorno)
    if problema == "sin_evidencia":
        estado.evidencia = []
    elif problema == "ajena":
        estado.evidencia[0].chunk.workspace_id = "ws_ajeno"
    elif problema == "hash":
        estado.evidencia[0].chunk.document_hash = "viejo"
    elif problema == "sin_trazabilidad":
        estado.trazabilidad = None
    elif problema == "fuente":
        entorno[1].fuente.hash = "otra_version"
    elif problema == "cancelacion":
        entorno[0].grafo.ejecucion._evento_cancelacion.set()
    else:
        entorno[0].grafo.ejecucion.deadline = 1
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status in ("failed", "cancelled") and nuevo.borrador is None
    assert nuevo.presupuesto.usadas == entorno[0].proveedor.llamadas_generacion == 0


def test_cancelacion_en_vuelo_descarta_respuesta(entorno):
    estado = estado_inicial(entorno)
    proveedor = entorno[0].proveedor

    def cancelar(*args, **kwargs):
        proveedor.llamadas_generacion += 1
        entorno[0].grafo.ejecucion._evento_cancelacion.set()
        return json.dumps(respuesta(estado))

    proveedor.generar_sync = cancelar
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "cancelled" and nuevo.borrador is None and nuevo.presupuesto.usadas == 1


def test_fuente_cambiada_en_vuelo_descarta_respuesta(entorno):
    estado = estado_inicial(entorno)
    proveedor = entorno[0].proveedor

    def cambiar(*args, **kwargs):
        proveedor.llamadas_generacion += 1
        entorno[1].fuente.hash = "version_nueva"
        return json.dumps(respuesta(estado))

    proveedor.generar_sync = cambiar
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "failed" and nuevo.error.code == "INVALID_STATE"
    assert nuevo.borrador is None and nuevo.presupuesto.usadas == 1


def test_borrador_anterior_no_se_conserva_tras_fallo_tecnico(entorno):
    estado = estado_inicial(entorno)
    entorno[0].proveedor.programar_generacion(json.dumps(respuesta(estado)))
    anterior = aplicar(estado, writer(estado, entorno[0]))
    entorno[0].proveedor.programar_generacion(*(ReintentableError("fallo") for _ in range(3)))
    nuevo = aplicar(anterior, writer(anterior, entorno[0]))
    assert anterior.borrador is not None
    assert nuevo.status == "failed" and nuevo.borrador is None and not nuevo.referencias
    assert nuevo.presupuesto.usadas == 4 and nuevo.intento == 2


def test_fallo_inesperado_conserva_contador_sin_exponer_mensaje(entorno):
    estado = estado_inicial(entorno)
    entorno[0].proveedor.programar_generacion(RuntimeError("TEST_SECRET"))
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.status == "failed" and nuevo.error.code == "INTERNAL"
    assert nuevo.presupuesto.usadas == nuevo.intento == 1
    assert nuevo.borrador is None and "TEST_SECRET" not in nuevo.model_dump_json()


def test_metadata_numerica_no_se_coerciona_desde_booleano(entorno):
    estado = estado_inicial(entorno)
    invalida = respuesta(estado)
    invalida["metadatos"]["tiempo_estimado_estudio_minutos"] = True
    entorno[0].proveedor.programar_generacion(json.dumps(invalida), json.dumps(respuesta(estado)))
    nuevo = aplicar(estado, writer(estado, entorno[0]))
    assert nuevo.intento == 2 and nuevo.borrador.metadatos.tiempo_estimado_estudio_minutos == 1


def test_mock_es_explicito_y_dependencias_no_filtran_secretos(entorno):
    with pytest.raises(ValueError, match="MOCK_GEMINI"):
        DependenciasWriter(
            entorno[0].grafo,
            entorno[0].proveedor,
            Configuracion(app_env="test", mock_oci=True, mock_gemini=False),
        )
    assert "google_api_key" not in repr(entorno[0])
    with pytest.raises(ValidationError):
        Configuracion(app_env="production", mock_gemini=True)


def cliente_http(entorno, handler):
    config = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=False,
        google_api_key="test-key",
        gemini_generation_model=MODELO,
        generation_max_output_tokens=1000,
    )
    http = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = genai.Client(api_key="test-key", http_options=types.HttpOptions(httpx_client=http))
    proveedor = ClienteGeminiGeneracion(config, cliente=sdk)
    return DependenciasWriter(entorno[0].grafo, proveedor, config)


def test_sdk_real_con_transporte_simulado_no_anade_retries(entorno):
    estado = estado_inicial(entorno)
    solicitudes = []

    def handler(request):
        solicitudes.append(request)
        if len(solicitudes) < 3:
            return httpx.Response(
                503, json={"error": {"code": 503, "message": "temporal"}}, headers={"Retry-After": "0"}
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": json.dumps(respuesta(estado))}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    deps = cliente_http(entorno, handler)
    try:
        nuevo = aplicar(estado, writer(estado, deps))
        assert nuevo.borrador is not None and nuevo.intento == 1 and nuevo.presupuesto.usadas == 3
        assert len(solicitudes) == 3
        body = json.loads(solicitudes[0].content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseJsonSchema"]["required"] == ["contenido_adaptado", "metadatos"]
        assert body["generationConfig"]["maxOutputTokens"] == 1000
        assert "Writer" in body["systemInstruction"]["parts"][0]["text"]
        assert solicitudes[0].extensions["timeout"]["read"] == 60
    finally:
        deps.proveedor.cerrar()


@pytest.mark.parametrize("diaria", [False, True])
def test_sdk_429_retry_after_y_cuota_diaria(entorno, diaria):
    estado = estado_inicial(entorno)
    solicitudes, pausas = [], []
    entorno[0].grafo.ejecucion._evento_cancelacion.wait = lambda segundos: pausas.append(segundos)

    def handler(request):
        solicitudes.append(request)
        detalles = (
            [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}],
                }
            ]
            if diaria
            else []
        )
        return httpx.Response(
            429, json={"error": {"code": 429, "message": "cuota", "details": detalles}}, headers={"Retry-After": "2"}
        )

    deps = cliente_http(entorno, handler)
    try:
        nuevo = aplicar(estado, writer(estado, deps))
        assert nuevo.status == "failed" and nuevo.borrador is None
        assert len(solicitudes) == nuevo.presupuesto.usadas == (1 if diaria else 3)
        assert nuevo.error.code == ("RATE_LIMITED" if diaria else "PROVIDER_UNAVAILABLE")
        assert pausas == ([] if diaria else [2, 2])
    finally:
        deps.proveedor.cerrar()


def test_sdk_respuesta_truncada_no_se_convierte_en_borrador(entorno):
    estado = estado_inicial(entorno)
    deps = cliente_http(
        entorno,
        lambda request: httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(respuesta(estado))}]}, "finishReason": "MAX_TOKENS"}
                ]
            },
        ),
    )
    try:
        nuevo = aplicar(estado, writer(estado, deps))
        assert nuevo.status == "failed" and nuevo.error.code == "PROVIDER_UNAVAILABLE"
        assert nuevo.borrador is None and nuevo.presupuesto.usadas == 1
    finally:
        deps.proveedor.cerrar()


def test_sdk_fallo_permanente_no_reintenta_ni_expone_payload(entorno):
    estado = estado_inicial(entorno)
    solicitudes = []

    def handler(request):
        solicitudes.append(request)
        return httpx.Response(403, json={"error": {"code": 403, "message": "TEST_SECRET"}})

    deps = cliente_http(entorno, handler)
    try:
        nuevo = aplicar(estado, writer(estado, deps))
        assert nuevo.status == "failed" and nuevo.error.code == "PROVIDER_UNAVAILABLE"
        assert len(solicitudes) == nuevo.presupuesto.usadas == 1
        assert "TEST_SECRET" not in nuevo.model_dump_json()
    finally:
        deps.proveedor.cerrar()


def test_sdk_timeout_de_transporte_cuenta_tres_intentos(entorno):
    estado = estado_inicial(entorno)
    solicitudes = []

    def handler(request):
        solicitudes.append(request)
        raise httpx.ReadTimeout("timeout simulado", request=request)

    deps = cliente_http(entorno, handler)
    try:
        nuevo = aplicar(estado, writer(estado, deps))
        assert nuevo.status == "failed" and nuevo.borrador is None
        assert len(solicitudes) == nuevo.presupuesto.usadas == 3 and nuevo.intento == 1
    finally:
        deps.proveedor.cerrar()


def test_sdk_retry_after_http_date(entorno):
    from app.core.agents.gemini_generation import _retry_after
    from google.genai import errors

    fecha = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    response = httpx.Response(503, headers={"Retry-After": fecha})
    assert 28 <= _retry_after(errors.APIError(503, {}, response)) <= 30


def test_sdk_retry_info_sin_header():
    from app.core.agents.gemini_generation import _retry_after
    from google.genai import errors

    detalles = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"}]}}
    assert _retry_after(errors.APIError(429, detalles)) == 12.5
