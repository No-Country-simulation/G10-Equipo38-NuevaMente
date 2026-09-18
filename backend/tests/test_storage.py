"""Tests del StorageProvider mock (issue #04).

Verificación pedida por el issue: `pytest backend/tests/test_storage.py`
con AMBAS configuraciones de la fábrica (MOCK_OCI=1 y MOCK_OCI=0).

Qué cubre cada grupo:

1. Ciclo completo put/get/list/delete sin red (criterio de aceptación 1).
2. Prefijos oficiales de §8.3: las claves que usará producción funcionan
   igual en el mock y el listado por prefijo aísla cada familia.
3. Semántica condicional: crear_solo, if_match y sus conflictos — lo que
   manifiestos y progreso necesitan para no perder actualizaciones (§7.2).
4. Paginación con cursor: ningún listado se traga todo de una vez.
5. Fábrica con MOCK_OCI=0 sin credenciales: error de arranque con mensaje
   accionable, y JAMÁS un mock como reemplazo (criterios 2 y 3).
6. Higiene de claves: object_name que intenten escapar del directorio base
   (path traversal) se rechazan antes de tocar el sistema de archivos.

Nota para quien no conozca pytest: `tmp_path` es un directorio temporal
FRESCO que pytest crea por test (y borra después); `monkeypatch.setenv`
modifica variables de entorno solo durante el test y las restaura al
terminar. Juntos permiten probar el mock y la fábrica sin tocar la
configuración de la máquina ni el .data real del proyecto.
"""

import pytest
from app.storage.oci_storage import (
    BUCKET_PRODUCCION,
    VARIABLES_REQUERIDAS_REAL,
    LocalMockStorageProvider,
    get_storage_provider,
)
from app.storage.provider import (
    StorageConflict,
    StorageInvalidName,
    StorageNotFound,
    StoragePage,
    StorageProvider,
    StoredObject,
)


@pytest.fixture
def mock(tmp_path):
    """Un proveedor mock apuntando a un directorio temporal aislado."""
    return LocalMockStorageProvider(base_dir=tmp_path / "oci_mock_storage")


def test_ciclo_completo_sin_red(mock):
    """Criterio 1: put/get/get_as_text/list/delete con MOCK_OCI=1, sin red."""
    guardado = mock.upload("workspaces/ws_1/manifest.json", '{"version": 1}')

    assert isinstance(guardado, StoredObject)
    assert guardado.proveedor == "mock"
    assert guardado.uri.startswith("mock://")  # rótulo para la UI (§8.2)
    assert guardado.etag  # huella no vacía

    assert mock.get("workspaces/ws_1/manifest.json") == b'{"version": 1}'
    assert mock.get_as_text("workspaces/ws_1/manifest.json") == '{"version": 1}'

    pagina = mock.list("workspaces/")
    assert [item.object_name for item in pagina.items] == ["workspaces/ws_1/manifest.json"]

    mock.delete("workspaces/ws_1/manifest.json")
    with pytest.raises(StorageNotFound):
        mock.get("workspaces/ws_1/manifest.json")


def test_prefijos_oficiales_de_produccion(mock):
    """Las claves de §8.3 funcionan en el mock y los prefijos aíslan familias."""
    claves = [
        "workspaces/ws_1/manifest.json",
        "source_documents/ws_1/doc_1/original",
        "source_documents/ws_1/doc_1/manifest.json",
        "outputs/ws_1/gen_1/content.json",
        "exports/ws_1/gen_1/pdf",
        "progress/ws_1/state.json",
        "demo/redes.pdf",
    ]
    for clave in claves:
        mock.upload(clave, b"x")

    assert [i.object_name for i in mock.list("outputs/").items] == ["outputs/ws_1/gen_1/content.json"]
    assert len(mock.list("source_documents/").items) == 2
    assert len(mock.list("workspaces/").items) == 1
    # Sin prefijo: todo el "bucket".
    assert len(mock.list().items) == len(claves)


def test_crear_solo_no_pisa(mock):
    """crear_solo=True es la creación sin sobrescritura: segundo intento = conflicto."""
    mock.upload("workspaces/ws_1/manifest.json", "v1", crear_solo=True)
    with pytest.raises(StorageConflict):
        mock.upload("workspaces/ws_1/manifest.json", "v2", crear_solo=True)
    # El contenido original quedó intacto.
    assert mock.get_as_text("workspaces/ws_1/manifest.json") == "v1"


def test_actualizacion_condicional_por_etag(mock):
    """if_match implementa el control de versión de §7.2 para manifiestos/progreso."""
    primera = mock.upload("progress/ws_1/state.json", '{"v": 1}')

    # Otra "pestaña" escribe primero: la huella que yo leí queda vieja.
    mock.upload("progress/ws_1/state.json", '{"v": 2}')
    with pytest.raises(StorageConflict):
        mock.upload("progress/ws_1/state.json", '{"v": 3-mia"}', if_match=primera.etag)

    # Con la huella ACTUAL la actualización sí pasa.
    actual = mock.upload("progress/ws_1/state.json", '{"v": 3}')
    assert mock.get_as_text("progress/ws_1/state.json") == '{"v": 3}'
    assert actual.etag != primera.etag  # la huella cambia con el contenido

    # Actualizar un objeto inexistente es NotFound, no un create disfrazado.
    with pytest.raises(StorageNotFound):
        mock.upload("progress/ws_9/state.json", "nuevo", if_match=primera.etag)

    # Lectura condicional: if_match correcto pasa; huella vieja falla.
    assert mock.get("progress/ws_1/state.json", if_match=actual.etag) == b'{"v": 3}'
    with pytest.raises(StorageConflict):
        mock.get("progress/ws_1/state.json", if_match=primera.etag)


def test_condiciones_mutuamente_excluyentes(mock):
    with pytest.raises(StorageInvalidName):
        mock.upload("a/b.json", "x", crear_solo=True, if_match="etag")


def test_etag_determinista_por_contenido(mock):
    """Mismo contenido => misma huella (útil para verificar escrituras idempotentes)."""
    a = mock.upload("demo/a", "mismo-contenido")
    b = mock.upload("demo/b", "mismo-contenido")
    distinto = mock.upload("demo/c", "otro-contenido")
    assert a.etag == b.etag
    assert a.etag != distinto.etag


def test_paginacion_con_cursor(mock):
    """El listado pagina por cursor: ni más ni menos que `limit` por página."""
    for indice in range(5):
        mock.upload(f"outputs/ws_1/gen_{indice}/content.json", b"x")

    pagina_1 = mock.list("outputs/", limit=2)
    assert [i.object_name for i in pagina_1.items] == [
        "outputs/ws_1/gen_0/content.json",
        "outputs/ws_1/gen_1/content.json",
    ]
    assert pagina_1.next_cursor == "outputs/ws_1/gen_1/content.json"

    pagina_2 = mock.list("outputs/", limit=2, cursor=pagina_1.next_cursor)
    assert [i.object_name for i in pagina_2.items] == [
        "outputs/ws_1/gen_2/content.json",
        "outputs/ws_1/gen_3/content.json",
    ]

    pagina_3 = mock.list("outputs/", limit=2, cursor=pagina_2.next_cursor)
    assert [i.object_name for i in pagina_3.items] == ["outputs/ws_1/gen_4/content.json"]
    assert pagina_3.next_cursor is None
    assert isinstance(pagina_3, StoragePage)


def test_listados_reflejan_borrados(mock):
    mock.upload("demo/a", "1")
    mock.upload("demo/b", "2")
    mock.delete("demo/a")
    assert [i.object_name for i in mock.list("demo/").items] == ["demo/b"]
    with pytest.raises(StorageNotFound):
        mock.delete("demo/a")


def test_claves_invalidas_rechazadas(mock):
    """Path traversal y compañía: la clave NUNCA se convierte en ruta libre."""
    for clave_mala in (
        "../fuera/de/base",
        "/absoluta",
        "carpeta//doble",
        "carpeta/../escape",
        "con espacios.json",
        "con\\backslash",
    ):
        with pytest.raises(StorageInvalidName):
            mock.upload(clave_mala, "x")
    with pytest.raises(StorageInvalidName):
        mock.get("../secrets.env")


# ---------------------------------------------------------------------------
# Fábrica: ambas configuraciones (la verificación exacta del issue)
# ---------------------------------------------------------------------------


def test_factory_mock_oci_1_devuelve_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_OCI", "1")
    proveedor = get_storage_provider(base_dir=tmp_path / "mock_base")
    assert isinstance(proveedor, LocalMockStorageProvider)
    assert isinstance(proveedor, StorageProvider)
    # Y funciona de punta a punta (criterio 1: sin red).
    proveedor.upload("demo/prueba", "ok")
    assert proveedor.get_as_text("demo/prueba") == "ok"


def test_factory_mock_oci_0_sin_credenciales_falla_visible(tmp_path, monkeypatch):
    """Criterios 2 y 3: error de arranque accionable, nunca un mock silencioso."""
    monkeypatch.setenv("MOCK_OCI", "0")
    for variable in VARIABLES_REQUERIDAS_REAL:
        monkeypatch.delenv(variable, raising=False)

    from app.storage.provider import StorageConfigError

    with pytest.raises(StorageConfigError) as info:
        get_storage_provider(base_dir=tmp_path)

    mensaje = str(info.value)
    # Accionable: nombra CADA variable que falta y cómo salir (MOCK_OCI=1).
    for variable in VARIABLES_REQUERIDAS_REAL:
        assert variable in mensaje
    assert "MOCK_OCI=1" in mensaje


def test_factory_mock_oci_0_con_credenciales_sigue_fallando_hasta_el_issue_14(tmp_path, monkeypatch):
    """Con credenciales presentes pero sin proveedor real (#14 pendiente), el
    arranque tampoco fabrica un mock: el fallo sigue siendo visible."""
    monkeypatch.setenv("MOCK_OCI", "0")
    monkeypatch.setenv("OCI_BUCKET_NAME", BUCKET_PRODUCCION)
    monkeypatch.setenv("OCI_COMPARTMENT_ID", "ocid1.compartment.oc1..test")
    monkeypatch.setenv("OCI_REGION", "us-ashburn-1")
    monkeypatch.setenv("OCI_CONFIG_FILE", "/run/oci/config")

    from app.storage.provider import StorageConfigError

    with pytest.raises(StorageConfigError) as info:
        get_storage_provider(base_dir=tmp_path)
    assert "issue #14" in str(info.value)
