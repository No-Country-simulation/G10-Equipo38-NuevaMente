"""Contrato HTTP de presupuestos y revocación inmediata (#50)."""

import pytest
import test_api_generate as generaciones
from app.schemas.enums import DocumentStatus
from app.storage.provider import StorageUnavailable
from test_api_generate import esperar, generar

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def test_estado_uso_vencimiento_alerta_y_sin_datos_privados(entorno):
    e = entorno
    e.storage.presupuesto.bytes_maximos = 10000
    # Inventario conservador sintético para disparar la alerta en el HTTP real.
    e.storage.presupuesto.incorporar_inventario([("demo/capacidad-prueba", 8000)])
    res = e.cliente.get("/api/workspaces/current", headers=e.headers)
    assert res.status_code == 200
    datos = res.json()
    assert datos["workspace_id"] == e.ws["workspace_id"] and datos["expira_en"]
    assert datos["uso"] == {"documentos": 1, "limite_documentos": 5, "generaciones": 0, "limite_generaciones": 20}
    assert datos["almacenamiento"]["alerta"]
    assert not any(secreto in res.text for secreto in (e.ws["token"], e.ws["recovery_code"], "capacidad-prueba"))
    assert e.cliente.get("/api/workspaces/current").status_code == 401


def test_limites_http_antes_de_storage_o_llm(entorno):
    e = entorno
    ws = e.ws["workspace_id"]
    for n in range(4):
        e.db.registrar_documento("doc_extra_" + str(n), ws, "Red", DocumentStatus.READY)
    subida = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers,
        json={"documento_titulo": "Red", "documento_contenido": "Una subred privada"},
    )
    assert subida.status_code == 429 and subida.json()["error"]["details"]["limite"] == 5
    for n in range(20):
        e.db.registrar_generacion("gen_extra_" + str(n), ws, e.doc)
    antes = e.doble.llamadas_generacion
    res = generar(e)
    assert res.status_code == 429 and res.json()["error"]["details"]["limite"] == 20
    assert e.doble.llamadas_generacion == antes


def test_borrar_http_confirma_revocacion_con_storage_caido(entorno, monkeypatch):
    e = entorno
    monkeypatch.setattr(e.storage, "upload", lambda *a, **k: (_ for _ in ()).throw(StorageUnavailable("privado")))
    assert e.cliente.delete("/api/workspaces/current", headers=e.headers).status_code == 204
    assert e.cliente.get("/api/workspaces/current", headers=e.headers).status_code == 401
    assert e.cliente.post("/api/sessions/recover", json={"recovery_code": e.ws["recovery_code"]}).status_code == 401
    assert e.db.limpiezas_espacios()[0]["estado"] == "pendiente"


def test_purga_real_de_original_indice_generacion_exports_y_sqlite(entorno):
    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    ws = e.ws["workspace_id"]
    assert e.storage.list("exports/" + ws + "/").items
    assert e.cliente.delete("/api/workspaces/current", headers=e.headers).status_code == 204
    e.app.state.workspace_lifecycle.reconciliar()
    assert e.db.limpiezas_espacios()[0]["estado"] == "datos_purgados"
    for raiz in ("source_documents", "outputs", "exports", "progress"):
        assert not e.storage.list(f"{raiz}/{ws}/").items
    assert not e.app.state.vectorstore._privada.get(where={"workspace_id": ws})["ids"]
    assert e.db.recursos_espacio(ws) == ([], [], False)
    assert e.db.obtener_sesion(e.ws["token"]) is None


def test_limites_personalizados_en_resumen_y_rechazo_http(tmp_path):
    from types import SimpleNamespace

    from app.config import Configuracion
    from app.jobs.manager import CuotasProveedor
    from app.jobs.quotas import CuotasModelo
    from app.main import crear_app
    from starlette.testclient import TestClient
    from test_api_generate import DobleEducativo

    cfg = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(tmp_path),
        embedding_dimensions=16,
        workspace_max_documents=2,
        workspace_max_generations=2,
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
        headers = {"Authorization": "Bearer " + ws["token"]}
        res = cliente.get("/api/workspaces/current", headers=headers)
        assert res.status_code == 200
        assert res.json()["uso"]["limite_documentos"] == 2
        assert res.json()["uso"]["limite_generaciones"] == 2

        # 1er documento
        subida1 = cliente.post(
            "/api/documents/upload",
            headers=headers,
            json={"documento_titulo": "Doc 1", "documento_contenido": "Contenido de prueba 1. " * 10},
        )
        assert subida1.status_code == 202
        esperar(SimpleNamespace(cliente=cliente, headers=headers), subida1.json())
        doc1_id = subida1.json()["document_id"]

        # 2do documento
        subida2 = cliente.post(
            "/api/documents/upload",
            headers=headers,
            json={"documento_titulo": "Doc 2", "documento_contenido": "Contenido de prueba 2. " * 10},
        )
        assert subida2.status_code == 202
        esperar(SimpleNamespace(cliente=cliente, headers=headers), subida2.json())

        # 3er documento -> 429 RATE_LIMITED con limite: 2
        subida3 = cliente.post(
            "/api/documents/upload",
            headers=headers,
            json={"documento_titulo": "Doc 3", "documento_contenido": "Contenido de prueba 3. " * 10},
        )
        assert subida3.status_code == 429
        err = subida3.json()["error"]
        assert err["code"] == "RATE_LIMITED"
        assert err["details"]["motivo"] == "workspace_resource_limit"
        assert err["details"]["recurso"] == "documents"
        assert err["details"]["limite"] == 2
        assert err["details"]["usados"] == 2

        # Generaciones: registrar 2 generaciones y comprobar rechazo en la 3ra
        app.state.db.registrar_generacion("gen_custom_0", ws["workspace_id"], doc1_id)
        app.state.db.registrar_generacion("gen_custom_1", ws["workspace_id"], doc1_id)
        res_gen = cliente.post(
            "/api/generate",
            headers=headers,
            json={
                "document_id": doc1_id,
                "perfil_destinatario": "principiante",
                "formato_salida": "flashcards",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "idioma_salida": "es",
            },
        )
        assert res_gen.status_code == 429
        err_g = res_gen.json()["error"]
        assert err_g["code"] == "RATE_LIMITED"
        assert err_g["details"]["motivo"] == "workspace_resource_limit"
        assert err_g["details"]["recurso"] == "generations"
        assert err_g["details"]["limite"] == 2


def test_bajar_limite_no_purga_datos_existentes_y_bloquea_nuevos(tmp_path):
    from types import SimpleNamespace

    from app.config import Configuracion
    from app.jobs.manager import CuotasProveedor
    from app.jobs.quotas import CuotasModelo
    from app.main import crear_app
    from starlette.testclient import TestClient
    from test_api_generate import DobleEducativo

    # 1. Crear espacio con límite default (5) y 3 documentos
    cfg1 = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(tmp_path),
        embedding_dimensions=16,
        workspace_max_documents=5,
    )
    doble = DobleEducativo(16)
    cuotas = CuotasProveedor(
        {
            m: CuotasModelo(1000, 10_000_000, 1000)
            for m in (cfg1.gemini_embedding_model, cfg1.gemini_generation_model, cfg1.gemini_verification_model)
        }
    )
    app1 = crear_app(
        cfg1,
        proveedor_embeddings=doble,
        proveedor_generacion=doble,
        proveedor_verificacion=doble,
        cuotas_proveedor=cuotas,
    )
    with TestClient(app1) as cliente1:
        ws = cliente1.post("/api/workspaces").json()
        headers = {"Authorization": "Bearer " + ws["token"]}
        docs = []
        for i in range(3):
            subida = cliente1.post(
                "/api/documents/upload",
                headers=headers,
                json={"documento_titulo": f"Doc {i}", "documento_contenido": f"Contenido {i}. " * 10},
            )
            assert subida.status_code == 202
            esperar(SimpleNamespace(cliente=cliente1, headers=headers), subida.json())
            docs.append(subida.json()["document_id"])

    # 2. Reiniciar servicio reduciendo el límite a 2 (inferior a los 3 ya conservados)
    cfg2 = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(tmp_path),
        embedding_dimensions=16,
        workspace_max_documents=2,
    )
    app2 = crear_app(
        cfg2,
        proveedor_embeddings=doble,
        proveedor_generacion=doble,
        proveedor_verificacion=doble,
        cuotas_proveedor=cuotas,
    )
    with TestClient(app2) as cliente2:
        res = cliente2.get("/api/workspaces/current", headers=headers)
        assert res.status_code == 200
        # No se purgan los datos: siguen estando los 3 documentos
        assert res.json()["uso"]["documentos"] == 3
        assert res.json()["uso"]["limite_documentos"] == 2
        for doc_id in docs:
            assert app2.state.db.obtener_documento(doc_id) is not None

        # Nueva admisión queda bloqueada
        subida_bloqueada = cliente2.post(
            "/api/documents/upload",
            headers=headers,
            json={"documento_titulo": "Doc Extra", "documento_contenido": "Extra. " * 10},
        )
        assert subida_bloqueada.status_code == 429
        err = subida_bloqueada.json()["error"]
        assert err["details"]["limite"] == 2
        assert err["details"]["usados"] == 3

        # Al agendar borrado de 2 documentos, usados baja a 1 (< 2), liberando cupo
        app2.state.db.agendar_borrado("document", docs[0], ws["workspace_id"])
        app2.state.db.agendar_borrado("document", docs[1], ws["workspace_id"])
        subida_desbloqueada = cliente2.post(
            "/api/documents/upload",
            headers=headers,
            json={"documento_titulo": "Doc Nuevo", "documento_contenido": "Nuevo. " * 10},
        )
        assert subida_desbloqueada.status_code == 202
