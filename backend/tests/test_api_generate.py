"""API #31 con Chroma, worker y StateGraph reales; proveedores simulados explícitos."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.core.agents.prompts import ejemplo_few_shot
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.main import crear_app
from app.schemas.requests import GenerateRequest
from app.storage.provider import StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from test_critic import rubrica

pytestmark = pytest.mark.integration_mock


class DobleEducativo(DobleGemini):
    rechazar = False
    bloquear = None

    def generar_sync(self, prompt, **kwargs):
        if self.bloquear is not None:
            self.bloquear.wait(5)
        datos = json.loads(prompt)
        p = datos["parametros"]
        chunk = datos["evidencia"][0]["chunk"]
        solicitud = GenerateRequest.model_validate(p)
        salida = ejemplo_few_shot(solicitud.perfil_destinatario, solicitud.formato_salida, solicitud.idioma_salida)
        salida = json.loads(
            json.dumps(salida)
            .replace("[SOURCE_CHUNK_ID]", chunk["chunk_id"])
            .replace("[SOURCE_SECTION]", chunk["seccion"] or "Fuente")
        )
        salida["metadatos"].update(
            idioma_origen=datos["idioma_origen"],
            nicho_sector=p["nicho_sector"],
            nivel_detalle=p["nivel_detalle"],
            alcance={**p["alcance"], "secciones_cubiertas": datos["secciones_cubiertas"]},
        )

        def citas(valor):
            if isinstance(valor, dict):
                for clave, item in valor.items():
                    if clave == "referencias":
                        valor[clave] = [{k: chunk[k] for k in ("chunk_id", "pagina", "seccion")}]
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
                    for ruta in list(datos["segmentos"])[:1]
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
            salida = {**rubrica(), "claridad_pedagogica": "baja" if self.rechazar else "alta"}
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
def entorno(tmp_path, aislar_estado_seguridad):
    cfg = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleEducativo(16)
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
        )
        subida = cliente.post(
            "/api/documents/upload",
            headers=e.headers,
            json={
                "documento_titulo": "Redes VCN",
                "documento_contenido": "Una VCN conecta subredes privadas y conserva aislamiento. " * 20,
            },
        )
        assert subida.status_code == 202, subida.text
        esperar(e, subida.json())
        e.doc = subida.json()["document_id"]
        e.solicitud = dict(
            document_id=e.doc,
            perfil_destinatario="principiante",
            formato_salida="flashcards",
            nicho_sector="general",
            nivel_detalle="didactico",
            idioma_salida="es",
        )
        yield e
        if doble.bloquear is not None:
            doble.bloquear.set()


def esperar(e, recibo, status="completed", *, timeout=60):
    """Espera un estado terminal del trabajo con red de seguridad amplia.

    El timeout NO es una aserción de velocidad: los estados failed/cancelled
    cortan de inmediato. Medido en equipos cargados una generación con dobles
    puede superar 10 s (incluye Chroma, SQLite y SSE); con el margen anterior
    de 15 s la suite se volvía intermitente bajo contención.
    """
    limite = time.monotonic() + timeout
    ultimo = None
    while time.monotonic() < limite:
        respuesta = e.cliente.get(recibo["status_url"], headers=e.headers)
        assert respuesta.status_code == 200, respuesta.text
        ultimo = respuesta.json()
        if ultimo["status"] == status:
            return ultimo
        if ultimo["status"] not in {"queued", "running"}:
            pytest.fail(str(ultimo))
        time.sleep(0.01)
    pytest.fail(str(ultimo))


def generar(e, clave=None, **parametros):
    return e.cliente.post(
        "/api/generate",
        json={**e.solicitud, **parametros},
        headers={**e.headers, **({"Idempotency-Key": clave} if clave else {})},
    )


def test_lecturas_aprobadas_y_reintento_idempotente_sin_proveedores(entorno):
    from app.generations.service import ServicioGeneraciones

    e = entorno
    recibo = generar(e, "generacion-aprobada").json()
    salida = esperar(e, recibo)
    indice = e.app.state.documents_query.indice
    e.app.state.documents_query.indice = None
    e.app.state.generations = ServicioGeneraciones(
        e.db, e.storage, e.app.state.documents_query, e.gestor, e.app.state.config
    )
    try:
        assert e.cliente.get(recibo["status_url"], headers=e.headers).json()["contenido"] == salida["contenido"]
        assert (
            e.cliente.get("/api/generations", headers=e.headers).json()["items"][0]["generation_id"]
            == recibo["generation_id"]
        )
        assert e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers).status_code == 200
        assert generar(e, "generacion-aprobada").json()["generation_id"] == recibo["generation_id"]
        assert generar(e, "nueva-generacion").status_code == 503
    finally:
        e.app.state.documents_query.indice = indice


def test_feliz_sse_reconexion_y_ajeno(entorno):
    e = entorno
    respuesta = generar(e, "una-intencion")
    assert respuesta.status_code == 202, respuesta.text
    recibo = respuesta.json()
    paquete = esperar(e, recibo)
    assert paquete["contenido"]["almacenamiento_oci"]["objeto_id"].endswith("/content.json")
    assert paquete["persistencia"] == {"status_upload": "completado", "provider": "mock"}
    sse = e.cliente.get(recibo["events_url"], headers=e.headers)
    assert sse.headers["content-type"].startswith("text/event-stream")
    eventos = [json.loads(linea[6:]) for linea in sse.text.splitlines() if linea.startswith("data: ")]
    ids = [int(linea[4:]) for linea in sse.text.splitlines() if linea.startswith("id: ")]
    assert ids == sorted(set(ids)) and {"writer", "critic", "finalizer"} <= {x["step"] for x in eventos}
    assert all(x["generation_id"] == recibo["generation_id"] for x in eventos)
    llamadas = e.doble.llamadas_generacion
    assert generar(e, "una-intencion").json()["generation_id"] == recibo["generation_id"]
    assert e.doble.llamadas_generacion == llamadas
    assert e.cliente.get(recibo["events_url"], headers={**e.headers, "Last-Event-ID": str(ids[-1])}).text == ""
    assert generar(e, "una-intencion", idioma_salida="en").status_code == 409
    otro = e.cliente.post("/api/workspaces").json()
    ajeno = {"Authorization": "Bearer " + otro["token"]}
    for ruta in (recibo["status_url"], recibo["events_url"], "/api/exports/" + recibo["generation_id"]):
        assert e.cliente.get(ruta, headers=ajeno).status_code == 404


def test_oci_falla_y_persist_sin_llm(entorno, monkeypatch):
    e = entorno
    original = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("outputs/"):
            raise StorageUnavailable("secreto que no debe devolverse")
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    fallo = esperar(e, recibo, "failed")
    assert fallo["error"]["code"] == "STORAGE_UNAVAILABLE" and fallo["contenido"] is None
    assert e.db.obtener_documento(e.doc)["estado"] == "ready"
    llamadas = e.doble.llamadas_generacion + e.doble.llamadas_verificacion
    retenido = e.db.aprobado_retenido(recibo["generation_id"])
    assert retenido
    monkeypatch.setattr(e.storage, "upload", original)
    ruta = recibo["status_url"] + "/persist"
    res = e.cliente.post(ruta, headers={**e.headers, "Idempotency-Key": "persist-1"})
    assert res.status_code == 202, res.text
    esperar(e, recibo)
    assert e.cliente.post(ruta, headers=e.headers).json()["generation_id"] == recibo["generation_id"]
    assert llamadas == e.doble.llamadas_generacion + e.doble.llamadas_verificacion
    assert (
        e.storage.get("outputs/" + e.ws["workspace_id"] + "/" + recibo["generation_id"] + "/content.json")
        == retenido.encode()
    )
    assert e.db.aprobado_retenido(recibo["generation_id"]) is None


def test_rechazado_200_sin_contenido_export_ni_reintento(entorno):
    e = entorno
    e.doble.rechazar = True
    recibo = generar(e).json()
    fallo = esperar(e, recibo, "rejected_quality")
    assert fallo["contenido"] is None and fallo["error"]
    assert e.doble.llamadas_generacion == 3 and e.db.obtener_documento(e.doc)["estado"] == "ready"
    assert e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers).status_code == 409
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == 409


def test_quiz_estudiante_y_exportacion_canonica(entorno):
    e = entorno
    recibo = generar(e, formato_salida="quiz").json()
    resultado = esperar(e, recibo)
    assert "correct_option_id" not in json.dumps(resultado)
    assert "justificacion" not in json.dumps(resultado)
    canonico = e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers)
    assert canonico.status_code == 200 and "correct_option_id" in canonico.text


def test_cancelado_y_duplicate_en_curso(entorno):
    e = entorno
    e.doble.bloquear = threading.Event()
    recibo = generar(e, "doble-en-curso").json()
    assert generar(e, "doble-en-curso").json()["generation_id"] == recibo["generation_id"]
    res = e.cliente.post(recibo["cancel_url"], headers=e.headers)
    assert res.status_code == 200
    e.doble.bloquear.set()
    esperar(e, recibo, "cancelled")
    assert e.db.obtener_documento(e.doc)["estado"] == "ready"


def test_historial_keyset_y_filtros(entorno):
    e = entorno
    ids = []
    for _ in range(3):
        recibo = generar(e).json()
        esperar(e, recibo)
        ids.append(recibo["generation_id"])
    primera = e.cliente.get("/api/generations?limit=2&perfil=principiante", headers=e.headers).json()
    segunda = e.cliente.get(
        "/api/generations",
        params={"limit": 2, "perfil": "principiante", "cursor": primera["next_cursor"]},
        headers=e.headers,
    ).json()
    assert [x["generation_id"] for x in primera["items"] + segunda["items"]] == list(reversed(ids))
    assert segunda["next_cursor"] is None
    assert e.cliente.get("/api/generations?perfil=ejecutivo", headers=e.headers).json()["items"] == []
    assert (
        e.cliente.get("/api/generations", params={"cursor": primera["next_cursor"]}, headers=e.headers).status_code
        == 400
    )


def test_confirmacion_lectura_falla_y_repetir_subida_no_regenera(entorno, monkeypatch):
    e = entorno
    original = e.storage.get

    def lectura_inconsistente(clave, **kwargs):
        if clave.startswith("outputs/"):
            return b"objeto no confirmado"
        return original(clave, **kwargs)

    monkeypatch.setattr(e.storage, "get", lectura_inconsistente)
    recibo = generar(e).json()
    fallo = esperar(e, recibo, "failed")
    assert fallo["error"]["code"] == "STORAGE_UNAVAILABLE" and fallo["contenido"] is None
    llamadas = e.doble.llamadas_generacion + e.doble.llamadas_verificacion
    monkeypatch.setattr(e.storage, "get", original)
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == 202
    esperar(e, recibo)
    assert e.doble.llamadas_generacion + e.doble.llamadas_verificacion == llamadas


@pytest.mark.parametrize("retiro", ["vencido", "documento"])
def test_aprobado_retenido_vencido_o_retirado_no_se_publica(entorno, monkeypatch, retiro):
    e = entorno
    original = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("outputs/"):
            raise StorageUnavailable("fallo simulado")
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    esperar(e, recibo, "failed")
    monkeypatch.setattr(e.storage, "upload", original)
    llamadas = e.doble.llamadas_generacion + e.doble.llamadas_verificacion
    if retiro == "vencido":
        with e.db.transaccion():
            e.db._conn.execute("UPDATE approved_outputs SET expira_en='2000-01-01T00:00:00+00:00'")
        status = 409
    else:
        res = e.cliente.delete("/api/documents/" + e.doc, headers=e.headers)
        assert res.status_code in {202, 204}
        status = 404
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == status
    assert e.db._conn.execute("SELECT COUNT(*) FROM approved_outputs").fetchone()[0] == 0
    assert e.doble.llamadas_generacion + e.doble.llamadas_verificacion == llamadas
