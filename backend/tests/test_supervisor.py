"""Supervisor sin red: acceso, alcance, determinismo y contrato serializable."""

import json
from itertools import product
from pathlib import Path

import pytest
from app.core.agents.graph_state import DependenciasGrafo, DocumentoGeneracion, EstadoGrafo
from app.core.agents.supervisor import crear_estado_inicial, supervisor
from app.jobs.manager import ContextoEjecucion
from app.schemas.enums import DocumentStatus, OutputLanguage, PedagogicalFormat, RecipientProfile
from app.schemas.responses import DocumentoFuente
from pydantic import ValidationError

pytestmark = pytest.mark.unit

SOLICITUD = {
    "document_id": "doc_1",
    "perfil_destinatario": "principiante",
    "formato_salida": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_salida": "pt",
}


@pytest.fixture
def entorno(monkeypatch):
    ctx = ContextoEjecucion(job_id="job_1", workspace_id="ws_1", tipo="generacion", deadline=1e12)

    def llamada_prohibida(*args, **kwargs):
        pytest.fail("El Supervisor no debe gastar llamadas LLM")

    monkeypatch.setattr(ctx, "llamar", llamada_prohibida)
    documento = DocumentoGeneracion(
        workspace_id="ws_1",
        fuente=DocumentoFuente(document_id="doc_1", titulo="Documento técnico", hash="sha256:abc", version="v1"),
        estado=DocumentStatus.READY,
        idioma_origen="EN-us",
        secciones=("sec_1", "sec_2"),
    )
    consultas = []

    def obtener(workspace_id, document_id):
        consultas.append((workspace_id, document_id))
        return documento

    return DependenciasGrafo(ctx, obtener), documento, consultas


def crear(entorno, **cambios):
    return crear_estado_inicial({**SOLICITUD, **cambios}, generation_id="gen_1", dependencias=entorno[0])


def test_estado_inicial_snapshot_determinista(entorno):
    estado = crear(entorno)
    esperado = json.loads((Path(__file__).parent / "fixtures" / "supervisor_initial.json").read_text(encoding="utf-8"))
    assert estado.model_dump(mode="json") == esperado
    assert crear(entorno).model_dump(mode="json") == esperado
    assert estado.presupuesto.usadas == 0
    assert EstadoGrafo.model_validate_json(estado.model_dump_json()) == estado
    assert entorno[2] == [("ws_1", "doc_1"), ("ws_1", "doc_1")]


@pytest.mark.parametrize(
    "cambios",
    [
        {"formato_salida": "inexistente"},
        {"perfil_destinatario": "inventado"},
        {"idioma_salida": "de"},
        {"nivel_detalle": "otro"},
        {"nicho_sector": "otro"},
        {"document_id": " "},
        {"alcance": {"tipo": "seccion"}},
        {"token": "no-debe-aparecer"},
    ],
)
def test_parametros_invalidos_fallan_sin_llm_ni_lectura(entorno, cambios):
    estado = crear(entorno, **cambios)
    assert estado.status == "failed"
    assert estado.error.code == "VALIDATION_ERROR"
    assert estado.error.message
    assert estado.presupuesto.usadas == 0
    assert estado.borrador is estado.restricciones is estado.rubrica is None
    assert not entorno[2]
    assert "no-debe-aparecer" not in estado.model_dump_json()


@pytest.mark.parametrize("status", [DocumentStatus.PROCESSING, DocumentStatus.FAILED])
def test_documento_no_ready_falla(entorno, status):
    entorno[1].estado = status
    assert crear(entorno).error.code == "INVALID_STATE"


def test_vision_pendiente_no_pasa_anticipadamente(entorno):
    entorno[1].vision_pendiente = True
    assert crear(entorno).status == "failed"
    entorno[1].vision_pendiente = False
    entorno[1].contiene_material_visual = True
    assert crear(entorno).rubrica.requiere_revision_visual


@pytest.mark.parametrize("ajeno", ["workspace", "documento", "inexistente", "estado"])
def test_ownership_desde_runtime(entorno, ajeno):
    dependencias, documento, consultas = entorno
    if ajeno == "workspace":
        documento.workspace_id = "ws_ajeno"
    elif ajeno == "documento":
        documento.fuente.document_id = "doc_ajeno"
    elif ajeno == "inexistente":
        dependencias = DependenciasGrafo(dependencias.ejecucion, lambda *args: None)
    estado = crear_estado_inicial(SOLICITUD, generation_id="gen_1", dependencias=dependencias)
    if ajeno == "estado":
        estado.workspace_id = "ws_ajeno"
        consultas.clear()
        update = supervisor(estado, dependencias)
        assert not consultas
        assert update["status"] == "failed"
        assert update["error"].code == "NOT_FOUND"
    else:
        assert estado.status == "failed"
        assert estado.error.code == "NOT_FOUND"
        assert estado.documento_fuente is None
    assert estado.presupuesto.usadas == 0


def test_alcance_validado_sin_inventar_cobertura(entorno):
    estado = crear(entorno, alcance={"tipo": "seccion", "seccion_id": "sec_2"})
    assert estado.status == "running"
    assert estado.parametros.alcance.seccion_id == "sec_2"
    assert estado.secciones_cubiertas == []
    assert crear(entorno, alcance={"tipo": "seccion", "seccion_id": "ajena"}).error.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("origen,esperado", [("es_AR", "es"), ("en-US", "en"), ("pt-BR", "pt"), ("mixto", "mixto")])
def test_idioma_origen_no_pisa_salida(entorno, origen, esperado):
    entorno[1].idioma_origen = origen
    estado = crear(entorno, idioma_salida="es")
    assert estado.idioma_origen == esperado
    assert estado.parametros.idioma_salida == estado.restricciones.idioma_salida == "es"


def test_idioma_origen_no_soportado_falla(entorno):
    entorno[1].idioma_origen = "de"
    assert crear(entorno).error.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("perfil,formato,idioma", tuple(product(RecipientProfile, PedagogicalFormat, OutputLanguage)))
def test_plantillas_y_rubrica_disponibles(entorno, perfil, formato, idioma):
    estado = crear(entorno, perfil_destinatario=perfil, formato_salida=formato, idioma_salida=idioma)
    assert estado.status == "running"
    assert estado.restricciones.orientacion_perfil
    assert estado.restricciones.requisitos_formato
    assert estado.restricciones.max_redacciones == 3
    assert estado.restricciones.max_llamadas == 20
    assert "afirmacion_sin_respaldo" in estado.rubrica.bloqueos
    assert estado.rubrica.score_minimo == 0.85


def test_cancelacion_y_deadline_del_worker(entorno):
    entorno[0].ejecucion._evento_cancelacion.set()
    assert crear(entorno).status == "cancelled"
    entorno[0].ejecucion._evento_cancelacion.clear()
    entorno[0].ejecucion.deadline = 1
    assert crear(entorno).error.code == "DEADLINE"


def test_runtime_no_es_serializable_como_estado(entorno):
    estado = crear(entorno)
    for nombre in ("contexto", "token", "cliente_sdk", "lock"):
        with pytest.raises(ValidationError):
            EstadoGrafo.model_validate({**estado.model_dump(), nombre: entorno[0]})
    assert "ContextoEjecucion" not in json.dumps(EstadoGrafo.model_json_schema())
    with pytest.raises(ValidationError):
        EstadoGrafo.model_validate({**estado.model_dump(), "status": "completed"})


def test_supervisor_solo_actualiza_restricciones_y_metadata(entorno):
    estado = crear(entorno)
    antes = estado.model_dump_json()
    update = supervisor(estado, entorno[0])
    assert "evidencia" not in update and "borrador" not in update and "presupuesto" not in update
    assert estado.model_dump_json() == antes
