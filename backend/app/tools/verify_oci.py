"""Smoke OCI real explícito: originales/paquetes de prueba, listado y limpieza."""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import Configuracion
from app.storage.object_keys import clave_original, clave_output
from app.storage.oci_budget import PresupuestoOCI
from app.storage.oci_real import OCIObjectStorageProvider
from app.storage.provider import StorageConfigError, StorageNotFound, StorageUnavailable


def verificar(configuracion, *, crear_bucket=False):
    if configuracion.mock_oci:
        raise StorageConfigError("La prueba real requiere MOCK_OCI=0; un mock no acredita OCI")
    if not configuracion.oci_always_free_confirmed:
        raise StorageConfigError("Verificar asignación gratuita y consumo existente; OCI_ALWAYS_FREE_CONFIRMED=1")
    presupuesto = PresupuestoOCI(
        Path(configuracion.data_dir) / "oci_budget.sqlite3",
        solicitudes=configuracion.oci_max_requests_per_month,
        bytes_maximos=configuracion.oci_max_storage_bytes,
    )
    proveedor = OCIObjectStorageProvider(configuracion, presupuesto=presupuesto, crear_bucket=crear_bucket)
    nonce = uuid.uuid4().hex
    workspace = "ws_smoke_" + nonce
    objetos = {
        clave_original(workspace, "doc_smoke_" + nonce): ("Prueba técnica OCI " + nonce).encode(),
        clave_output(workspace, "gen_smoke_" + nonce): json.dumps(
            {
                "prueba_tecnica": True,
                "nonce": nonce,
                "descripcion": "Verificación de persistencia; no es contenido educativo aprobado",
            },
            ensure_ascii=False,
        ).encode(),
    }
    comprobados, limpieza = [], []
    fallo = None
    try:
        for nombre, contenido in objetos.items():
            guardado = proveedor.upload(nombre, contenido, crear_solo=True)
            if guardado.proveedor != "oci" or proveedor.get(nombre, if_match=guardado.etag) != contenido:
                raise StorageUnavailable("La escritura/lectura real no quedó confirmada")
            prefijo = nombre.rsplit("/", 1)[0] + "/"
            pagina = proveedor.list(prefijo, limit=10)
            if nombre not in [obj.object_name for obj in pagina.items]:
                raise StorageUnavailable("El objeto no aparece en el listado OCI")
            comprobados.append(nombre)
    except Exception as error:
        fallo = error
    finally:
        # Solo limpiar claves únicas de esta ejecución cuyo contenido coincide.
        for nombre, esperado in objetos.items():
            try:
                if proveedor.get(nombre) != esperado:
                    limpieza.append(nombre)
                    continue
                proveedor.delete(nombre)
            except StorageNotFound:
                pass
            except Exception:
                limpieza.append(nombre)
        proveedor.cerrar()
    if fallo is not None:
        raise fallo
    if limpieza:
        raise StorageUnavailable("La verificación no confirmó la limpieza de sus temporales OCI")
    resumen = presupuesto.resumen()
    return dict(
        verificacion="OCI real",
        run_id=nonce,
        verified_at=datetime.now(timezone.utc).isoformat(),
        namespace=proveedor.namespace,
        sdk="oci==2.187.2",
        region=configuracion.oci_region,
        bucket=configuracion.oci_bucket_name,
        originales_y_paquetes=len(comprobados),
        solicitudes_esta_ejecucion=proveedor.solicitudes,
        temporales_eliminados=True,
        presupuesto=resumen,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env", help="Configuración OCI; nunca se imprime su contenido")
    parser.add_argument(
        "--create-bucket",
        action="store_true",
        help="Provisionamiento manual explícito si falta el bucket privado Standard",
    )
    parser.add_argument("--report", type=Path, help="Guardar únicamente evidencia de la prueba, sin credenciales")
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        print("El reporte ya existe: usar un nombre nuevo para no confundir snapshots anteriores con esta prueba.")
        return 2
    try:
        resultado = verificar(Configuracion(_env_file=args.env_file), crear_bucket=args.create_bucket)
        texto = json.dumps(resultado, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as archivo:
                archivo.write(texto + "\n")
        print(texto)
        return 0
    except Exception:
        # El SDK puede incluir URLs/headers sensibles en sus excepciones. No volcarlos.
        print(
            "Verificación OCI fallida. Revisar MOCK_OCI=0, capacidad Always Free confirmada, "
            "credenciales, región principal, bucket/permisos y presupuesto. No se acreditó una integración real."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
