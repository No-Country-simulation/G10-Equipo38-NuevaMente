"""Smoke real de Compose (#26), solo en un proyecto efímero con mocks explícitos.

Recrea contenedores, conserva el volumen y comprueba SQLite mediante recuperación
por API. El marcador del directorio de índices acredita el volumen, no Chroma (#17).
"""

import json
import subprocess
import textwrap
import uuid
from urllib.request import ProxyHandler, Request, build_opener


def compose(*args, entrada=None):
    return subprocess.run(
        ["docker", "compose", *args], input=entrada, text=True, capture_output=True, check=True
    ).stdout


def inspeccionar(servicio):
    identificador = compose("ps", "--quiet", servicio).strip()
    datos = subprocess.run(["docker", "inspect", identificador], text=True, capture_output=True, check=True)
    return json.loads(datos.stdout)[0]


def main():
    config = json.loads(compose("config", "--format", "json"))
    entorno = config["services"]["backend"]["environment"]
    if entorno.get("MOCK_OCI") != "1" or entorno.get("MOCK_GEMINI") != "1":
        raise RuntimeError("Este smoke exige MOCK_OCI=1 y MOCK_GEMINI=1 en un proyecto efímero")
    if entorno.get("APP_ENV") not in ("development", "ci", "test"):
        raise RuntimeError("Este smoke no se ejecuta en producción")
    backend, frontend = inspeccionar("backend"), inspeccionar("frontend")
    for servicio, puerto in ((backend, "8000/tcp"), (frontend, "8501/tcp")):
        assert servicio["Config"]["User"] == "10001:10001"
        assert servicio["HostConfig"]["ReadonlyRootfs"]
        assert servicio["State"]["Health"]["Status"] == "healthy"
        assert all(p["HostIp"] == "127.0.0.1" for p in servicio["HostConfig"]["PortBindings"][puerto])
    volumen = next(m["Name"] for m in backend["Mounts"] if m["Destination"] == "/app/.data")
    assert all(m.get("Name") != volumen for m in frontend["Mounts"])
    assert any(m["Destination"] == "/app/documents" and not m["RW"] for m in backend["Mounts"])

    http = build_opener(ProxyHandler({}))
    with http.open("http://127.0.0.1:8000/api/health", timeout=5) as r:
        assert json.load(r)["status"] == "ok"
    with http.open("http://127.0.0.1:8501/", timeout=5) as r:
        assert r.status == 200 and b"<html" in r.read().lower()
    with http.open(Request("http://127.0.0.1:8000/api/workspaces", method="POST"), timeout=5) as r:
        espacio = json.load(r)  # Código y token quedan solamente en memoria.
    marcador = "issue26-" + uuid.uuid4().hex
    compose(
        "exec",
        "-T",
        "backend",
        "python",
        "-",
        entrada=textwrap.dedent(f"""
            import os, sqlite3
            from pathlib import Path
            assert os.getuid() == 10001
            assert Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf').is_file()
            with sqlite3.connect('/app/.data/operativo.db') as con:
                assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            indice = Path('/app/.data/chroma')
            indice.mkdir(exist_ok=True)
            (indice / '{marcador}').write_text('{marcador}')
            # Esos datos se montan al ejecutar; nunca se copian dentro de la imagen.
            assert not Path('/app/.env').exists()
            assert not Path('/app/tests').exists()
        """),
    )
    compose("up", "--detach", "--force-recreate", "--wait", "--wait-timeout", "120")
    nuevo = inspeccionar("backend")
    assert nuevo["Id"] != backend["Id"]
    assert any(m.get("Name") == volumen for m in nuevo["Mounts"])
    compose(
        "exec",
        "-T",
        "backend",
        "python",
        "-",
        entrada=textwrap.dedent(f"""
            from pathlib import Path
            p = Path('/app/.data/chroma/{marcador}')
            assert p.read_text() == '{marcador}'
            p.unlink()
        """),
    )
    solicitud = Request(
        "http://127.0.0.1:8000/api/sessions/recover",
        data=json.dumps({"recovery_code": espacio["recovery_code"]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with http.open(solicitud, timeout=5) as r:
        recuperado = json.load(r)
    assert recuperado["workspace_id"] == espacio["workspace_id"]
    # El espacio de prueba se limpia usando la misma autorización de la aplicación.
    solicitud = Request(
        "http://127.0.0.1:8000/api/workspaces/current",
        headers={"Authorization": "Bearer " + recuperado["token"]},
        method="DELETE",
    )
    with http.open(solicitud, timeout=5) as r:
        assert r.status == 204
    compose(
        "exec",
        "-T",
        "frontend",
        "python",
        "-c",
        "from streamlit.testing.v1 import AppTest; app = AppTest.from_file('app.py').run(timeout=20); assert not app.exception",
    )
    print("OK: API/UI, loopback, usuario sin root, SQLite WAL y volumen tras recreación")


if __name__ == "__main__":
    main()
