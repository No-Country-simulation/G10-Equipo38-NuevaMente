"""Espera activamente a la API y reemplaza el proceso por Streamlit (#26)."""

import json
import math
import os
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def esperar_api(api_url: str, *, timeout: float = 60) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("API_HEALTH_WAIT_SECONDS debe ser positivo y finito")
    limite = time.monotonic() + timeout
    # La conexión entre servicios no debe pasar por un proxy del entorno.
    cliente = build_opener(ProxyHandler({}))
    while (restante := limite - time.monotonic()) > 0:
        try:
            with cliente.open(api_url.rstrip("/") + "/api/health", timeout=min(3, restante)) as respuesta:
                datos = json.loads(respuesta.read(4096))
                if respuesta.status == 200 and isinstance(datos, dict) and datos.get("status") == "ok":
                    return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(min(1, max(0, limite - time.monotonic())))
    raise TimeoutError("La API no estuvo disponible dentro del plazo de arranque")


def main() -> None:
    try:
        print("Esperando disponibilidad de la API...", flush=True)
        esperar_api(
            os.environ.get("API_URL", "http://backend:8000"),
            timeout=float(os.environ.get("API_HEALTH_WAIT_SECONDS", "60")),
        )
    except (TimeoutError, ValueError) as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    print("API disponible; iniciando Streamlit.", flush=True)
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"],
    )


if __name__ == "__main__":
    main()
