"""python -m app.documents.preload_cli --documents-dir documents [--check]."""

import argparse
import json
import secrets
import sys
import time
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.config import Configuracion
from app.core.agents.gemini_generation import ClienteGeminiGeneracion
from app.core.rag.embeddings import crear_cliente_embeddings
from app.core.rag.multimodal import InterpretadorVisual
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, VectorStoreChroma
from app.documents.preload import PrecargadorDemo, leer_fuentes
from app.jobs.manager import ControlesOperativos, CuotasProveedor, GestorTrabajos
from app.jobs.quotas import cuotas_configuradas
from app.jobs.runtime_lock import exclusividad_runtime
from app.jobs.store import RegistroOperativo
from app.storage.oci_storage import get_storage_provider


class CuotasAuditadas(CuotasProveedor):
    """Una entrada segura por solicitud admitida, incluidos intentos técnicos."""

    def __init__(self, limites, *, mock):
        super().__init__(limites)
        self.mock = mock

    def permitir(self, modelo, tokens_estimados=0):
        super().permitir(modelo, tokens_estimados)
        print(
            json.dumps(
                {
                    "evento": "solicitud_proveedor",
                    "gemini_mock": self.mock,
                    "modelo": modelo,
                    "tokens_estimados": tokens_estimados,
                    "fecha": datetime.now(timezone.utc).isoformat(),
                }
            ),
            flush=True,
        )


def ejecutar(configuracion, fuentes, *, proveedor_embeddings=None, proveedor_vision=None):
    """Proveedores dobles sólo por inyección de tests; la CLI nunca los inventa."""
    configuracion.validar_critico()
    if configuracion.mock_gemini and proveedor_embeddings is None:
        raise ValueError("La precarga CLI exige Gemini real; los dobles son exclusivos de pruebas por inyección")
    limites = cuotas_configuradas(configuracion)
    if configuracion.gemini_embedding_model not in limites:
        raise ValueError("Configurar las cuotas explícitas de embeddings antes del mantenimiento")
    if configuracion.mock_gemini and configuracion.gemini_generation_model in limites and proveedor_vision is None:
        raise ValueError("La visión mock exige un proveedor explícito de pruebas")
    data_dir = Path(configuracion.data_dir)
    with ExitStack() as recursos:
        recursos.enter_context(exclusividad_runtime(data_dir))
        db = RegistroOperativo(
            data_dir / "demo-mantenimiento.db",
            dias_retencion=configuracion.workspace_retention_days,
            limite_documentos=configuracion.workspace_max_documents,
            limite_generaciones=configuracion.workspace_max_generations,
        )
        recursos.callback(db.cerrar)
        workspace = "mantenimiento_" + secrets.token_hex(12)
        db.crear_workspace(workspace, secrets.token_urlsafe(32), 30)
        storage = get_storage_provider(configuracion=configuracion)
        cerrar = getattr(storage, "cerrar", None)
        if callable(cerrar):
            recursos.callback(cerrar)
        embeddings = crear_cliente_embeddings(configuracion=configuracion, proveedor=proveedor_embeddings)
        indice = VectorStoreChroma(data_dir / "chroma", embeddings)
        recursos.callback(indice.cerrar)
        vision = None
        if configuracion.gemini_generation_model in limites:
            proveedor_vision = proveedor_vision or ClienteGeminiGeneracion(configuracion)
            cerrar = getattr(proveedor_vision, "cerrar", None)
            if callable(cerrar):
                recursos.callback(cerrar)
            vision = InterpretadorVisual(
                proveedor_vision,
                modelo=configuracion.gemini_generation_model,
                mock=configuracion.mock_gemini,
                max_output_tokens=configuracion.generation_max_output_tokens,
            )
        gestor = GestorTrabajos(
            db,
            cuotas=CuotasAuditadas(limites, mock=configuracion.mock_gemini),
            controles=ControlesOperativos(
                deadline_ejecucion_segundos=configuracion.generation_deadline_seconds,
                max_en_cola=configuracion.max_queued_jobs,
            ),
        )
        recursos.callback(lambda: gestor.detener(timeout=None))
        precargador = PrecargadorDemo(storage, indice, vision=vision)
        resultados = []
        for fuente, contenido in fuentes:
            job_id = gestor.enqueue(
                workspace,
                "precarga_demo",
                lambda ctx, f=fuente, c=contenido: precargador.preparar(
                    replace(ctx, workspace_id=DEMO_WORKSPACE_ID), f, c
                ),
            )
            try:
                while (estado := gestor.estado(job_id))["status"] in ("queued", "running"):
                    time.sleep(0.05)
            except KeyboardInterrupt:
                gestor.cancelar(job_id)
                raise
            if estado["status"] != "completed":
                raise RuntimeError(
                    f"Precarga no confirmada: {estado['status']}; consultar el registro de mantenimiento."
                )
            resultado = json.loads(estado["resultado"])
            resultados.append(resultado)
            print(
                json.dumps(
                    {
                        "evento": "fuente_demo",
                        **resultado,
                        "oci_mock": configuracion.mock_oci,
                        "gemini_mock": configuracion.mock_gemini,
                    }
                ),
                flush=True,
            )
        return resultados


def main(argv=None):
    parser = argparse.ArgumentParser(description="Precarga offline de las tres fuentes demo; detener backend/frontend.")
    parser.add_argument("--documents-dir", type=Path, default=Path("documents"))
    parser.add_argument("--check", action="store_true", help="Validar archivos y parser sin proveedores ni escrituras.")
    args = parser.parse_args(argv)
    try:
        fuentes = leer_fuentes(args.documents_dir)
        if args.check:
            from app.core.rag.parser import parsear_archivo

            for fuente, contenido in fuentes:
                parseo = parsear_archivo(contenido, fuente.archivo)
                print(
                    json.dumps(
                        {
                            "document_id": fuente.document_id,
                            "tokens_textuales": parseo.cantidad_tokens,
                            "paginas_visuales": parseo.paginas_visuales,
                            "publicado": False,
                        }
                    )
                )
            return 0
        resultados = ejecutar(Configuracion(), fuentes)
        return 0 if all(r["estado"] == "ready" for r in resultados) else 2
    except Exception as error:
        # No volcar excepciones del SDK, rutas de credenciales ni contenido.
        print(
            f"Precarga no confirmada ({type(error).__name__}). Revisar configuración, exclusividad y registro.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
