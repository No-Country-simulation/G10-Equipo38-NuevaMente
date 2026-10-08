"""Transporte del SDK instalado: firma real con clave efímera, HTTP simulado."""

import io
import json
from urllib.parse import unquote, urlsplit

import oci
import pytest
from app.config import Configuracion
from app.storage.oci_real import OCIObjectStorageProvider
from app.storage.provider import StorageConfigError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pytestmark = pytest.mark.unit


@pytest.fixture
def archivo_oci(tmp_path):
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ruta = tmp_path / "clave-efimera.pem"
    ruta.write_bytes(
        clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    config = tmp_path / "config"
    config.write_text(
        "[DEFAULT]\nuser=ocid1.user.oc1..test\ntenancy=ocid1.tenancy.oc1..test\nfingerprint="
        + ":".join(["aa"] * 16)
        + "\nregion=us-ashburn-1\nkey_file="
        + ruta.as_posix()
        + "\n",
        encoding="utf-8",
    )
    return Configuracion(
        _env_file=None,
        mock_oci=False,
        mock_gemini=True,
        oci_always_free_confirmed=True,
        oci_compartment_id="ocid1.compartment.oc1..nm",
        oci_region="us-ashburn-1",
        oci_config_file=str(config),
        data_dir=str(tmp_path),
    )


@pytest.fixture
def transporte(monkeypatch):
    from oci._vendor import requests

    llamadas, objetos = [], {}

    def enviar(session, request, **kwargs):
        llamadas.append(request)
        assert request.headers.get("authorization")
        path = unquote(urlsplit(request.url).path)
        headers = {}
        if "regionSubscriptions" in path:
            dato = [{"regionName": "us-ashburn-1", "isHomeRegion": True, "status": "READY", "regionKey": "IAD"}]
        elif path.rstrip("/") == "/n":
            dato = "namespace-sdk"
        elif path.endswith("/o"):
            dato = {
                "objects": [
                    {"name": n, "size": len(v["body"]), "etag": v["etag"], "timeModified": "2026-10-08T00:00:00Z"}
                    for n, v in sorted(objetos.items())
                ]
            }
        elif "/o/" in path:
            nombre = path.split("/o/", 1)[1]
            if request.method == "PUT":
                assert request.headers["if-none-match"] == "*"
                assert request.headers.get("content-md5")
                objeto = dict(
                    body=request.body,
                    etag="etag-sdk",
                    headers={k: v for k, v in request.headers.items() if k.lower().startswith("opc-meta-")},
                )
                objetos[nombre] = objeto
                headers = {"etag": objeto["etag"]}
                dato = None
            elif request.method == "GET":
                objeto = objetos[nombre]
                headers = {"etag": objeto["etag"], **objeto["headers"]}
                dato = objeto["body"]
            else:
                del objetos[nombre]
                dato = None
        else:
            dato = {
                "compartmentId": "ocid1.compartment.oc1..nm",
                "storageTier": "Standard",
                "publicAccessType": "NoPublicAccess",
                "versioning": "Disabled",
                "autoTiering": "Disabled",
                "replicationEnabled": False,
                "kmsKeyId": None,
            }
        respuesta = requests.Response()
        respuesta.status_code = 200
        respuesta.headers.update(headers)
        respuesta.request = request
        respuesta._content = dato if isinstance(dato, bytes) else json.dumps(dato).encode()
        respuesta._content_consumed = True
        respuesta.raw = io.BytesIO(respuesta._content)
        return respuesta

    monkeypatch.setattr(requests.Session, "send", enviar)
    return llamadas, objetos


def test_sdk_real_serializa_condiciones_metadata_y_bytes(archivo_oci, transporte):
    llamadas, _ = transporte
    proveedor = OCIObjectStorageProvider(archivo_oci)
    try:
        assert isinstance(proveedor.cliente.retry_strategy, oci.retry.NoneRetryStrategy)
        guardado = proveedor.upload("demo/objeto", b"bytes-originales", crear_solo=True)
        assert proveedor.get("demo/objeto", if_match=guardado.etag) == b"bytes-originales"
        assert proveedor.list("demo/").items[0].object_name == "demo/objeto"
        proveedor.delete("demo/objeto")
        assert proveedor.presupuesto.resumen()["solicitudes"] == len(llamadas)
    finally:
        proveedor.cerrar()


def test_config_file_region_distinta_falla_sin_red(archivo_oci, transporte):
    llamadas, _ = transporte
    archivo_oci.oci_region = "sa-saopaulo-1"
    with pytest.raises(StorageConfigError, match="no coincide"):
        OCIObjectStorageProvider(archivo_oci)
    assert llamadas == []


def test_no_fallback_ante_datos_de_autenticacion_invalidos(archivo_oci, transporte):
    archivo_oci.oci_config_profile = "INEXISTENTE"
    with pytest.raises(StorageConfigError, match="OCI_CONFIG_PROFILE"):
        OCIObjectStorageProvider(archivo_oci)
    assert transporte[0] == []


def test_identidad_instancia_sin_archivo_y_sin_retries_adicionales(archivo_oci, monkeypatch):
    from types import SimpleNamespace

    from test_storage_oci import SDK

    sdk = SDK()
    signer = SimpleNamespace(region=archivo_oci.oci_region, tenancy_id="ocid1.tenancy.oc1..test")
    opciones = []

    def crear(ajustes, **kwargs):
        opciones.append(kwargs)
        assert ajustes == {"region": "us-ashburn-1"}
        assert kwargs["signer"] is signer
        return sdk

    def firmar(**kwargs):
        assert all(isinstance(v, oci.retry.NoneRetryStrategy) for v in kwargs.values())
        return signer

    sdk.base_client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", firmar)
    monkeypatch.setattr(oci.object_storage, "ObjectStorageClient", crear)
    monkeypatch.setattr(oci.identity, "IdentityClient", crear)
    archivo_oci.oci_auth_type = "instance_principal"
    archivo_oci.oci_config_file = "no-existe"
    proveedor = OCIObjectStorageProvider(archivo_oci)
    assert len(opciones) == 2 and all(isinstance(o["retry_strategy"], oci.retry.NoneRetryStrategy) for o in opciones)
    assert opciones[0]["timeout"] == (10, 30)
    proveedor.cerrar()


def test_sdk_no_suma_retries_ocultos_ante_503(archivo_oci, transporte, monkeypatch):
    from app.storage.provider import StorageUnavailable
    from oci._vendor import requests

    desde_sdk = requests.Session.send
    intentos = []

    def servicio_caido(session, request, **kwargs):
        if urlsplit(request.url).path.rstrip("/") == "/n":
            intentos.append(request)
            respuesta = requests.Response()
            respuesta.status_code = 503
            respuesta.headers.update({"Content-Type": "application/json"})
            respuesta.request = request
            respuesta._content = b'{"code":"Unavailable","message":"secreto"}'
            respuesta._content_consumed = True
            respuesta.raw = io.BytesIO(respuesta._content)
            return respuesta
        return desde_sdk(session, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", servicio_caido)
    with pytest.raises(StorageUnavailable) as info:
        OCIObjectStorageProvider(archivo_oci)
    assert len(intentos) == 3 and "secreto" not in str(info.value)
