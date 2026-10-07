# Contenedores locales — Issue 26

Docker Compose ejecuta el backend FastAPI y el frontend Streamlit. Conserva la
arquitectura de `decisiones_proyecto.md` §14 y `docs/arquitectura.md`: un proceso
Uvicorn, un worker dedicado y un volumen de datos exclusivo del backend.

## Arranque sin credenciales

Requisitos: Git, Docker Engine o Docker Desktop con contenedores Linux y Compose
v2 con soporte de `--wait`. No necesitás instalar Python en el host.

Desde la raíz del repositorio, en PowerShell, si todavía no existe `.env`:

```powershell
Copy-Item .env.docker.example .env
docker compose up --build --detach --wait --wait-timeout 120
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

En Linux/macOS, la copia equivalente es `cp .env.docker.example .env`.
Si ya tenés un `.env`, conservá su contenido y ajustá explícitamente
`APP_ENV=development`, `MOCK_OCI=1` y `MOCK_GEMINI=1` para este arranque local.
También podés usar el ejemplo sin copiarlo:

```powershell
$env:NUEVAMENTE_ENV_FILE = '.env.docker.example'
docker compose up --build --detach --wait --wait-timeout 120
```

- API: http://127.0.0.1:8000/api/health; OpenAPI: http://127.0.0.1:8000/docs.
- Interfaz: http://127.0.0.1:8501.
- Logs: `docker compose logs --tail 100`; estado: `docker compose ps`.
- Detener: `docker compose down`; volver a levantar conserva `backend_data`.
- Recrear: `docker compose up --detach --force-recreate --wait`.

Este modo utiliza almacenamiento local explícito. `MOCK_GEMINI=1` identifica el
entorno simulado; los tests inyectan su doble. No instala una integración de
producción ni completa los endpoints/pipelines todavía pendientes. El health
no consulta Gemini ni OCI. `.env.example` conserva el template de producción,
que exige credenciales reales y rechaza mocks.

## Probar embeddings con Gemini real

Creá una API key desde [Google AI Studio](https://aistudio.google.com/api-keys).
La API usa el proyecto, cuotas y [facturación de AI Studio](https://ai.google.dev/gemini-api/docs/billing/);
revisá allí el nivel Free/Paid disponible para tu cuenta. Tener una suscripción
de la app Gemini no acredita por sí solo las cuotas de la API.

Google AI Pro incluye USD 10 mensuales de créditos de Google Cloud mediante el
Google Developer Program, según [los beneficios oficiales](https://support.google.com/googleone/answer/14534406?hl=es-mx).
Vinculá tu suscripción al perfil de desarrollador y revisá el beneficio en
[tus beneficios](https://me.developers.google.com/benefits).
Comprobá que se aplique a la cuenta de facturación del proyecto usado en AI Studio.
Si esa cuenta usa Prepay, Google exige saldo prepago positivo antes de consumir
créditos promocionales; revisá el estado en [AI Studio Billing](https://aistudio.google.com/billing).
No se activa facturación ni se realizan pagos como parte de este issue.

Guardá la clave únicamente en tu `.env` local como `GOOGLE_API_KEY`, manteniendo
`APP_ENV=development`, `MOCK_OCI=1` y cambiando `MOCK_GEMINI=0`.
Si estabas usando `NUEVAMENTE_ENV_FILE=.env.docker.example`, quitá esa variable
con `Remove-Item Env:NUEVAMENTE_ENV_FILE` para volver a usar `.env`.
Recreá los servicios con `docker compose up --detach --force-recreate --wait`.
No pegues la clave en el chat ni dentro de comandos/versionado.

Esta prueba manual hace **una solicitud real** de embeddings, pasa por el gestor
central y muestra solamente la cantidad de dimensiones. Tiene cuotas locales
conservadoras para esta prueba; no son los límites universales de Google. La
respuesta esperada es `Dimensiones: 768`. Puede consumir cuota o saldo del
proyecto. La prueba de contenedores anterior funciona sin hacer esta llamada.

```powershell
@"
import time
from app.config import Configuracion
from app.core.rag.embeddings import crear_cliente_embeddings
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
cfg = Configuracion()
assert not cfg.mock_gemini, "Desactivá MOCK_GEMINI para esta prueba real"
cliente = crear_cliente_embeddings(configuracion=cfg)
ctx = ContextoEjecucion(
    job_id="prueba-manual", workspace_id="prueba-manual", tipo="embedding",
    deadline=time.monotonic() + 60, reintentos_transitorios=0,
    cuotas=CuotasProveedor({cfg.gemini_embedding_model: CuotasModelo(rpm=1, tpm=2000, rpd=1)}),
)
vector = cliente.embed_query("¿Qué es una red privada virtual?", contexto=ctx)
print("Dimensiones:", len(vector))
"@ | docker compose exec -T backend python -
```

Esto verifica el cliente #13 contra Gemini. El recorrido completo de cargar un
PDF y producir contenido desde la UI depende de otros issues; no se declara
implementado por esta prueba. Para volver al smoke de Compose, reactivá ambos
mocks explícitos y recreá los contenedores.

## Datos, red y arranque

`backend_data` se monta en `/app/.data` únicamente en el backend: SQLite WAL,
almacenamiento mock, temporales persistentes e índices. `documents/` es un bind
mount de solo lectura con la biblioteca demo, fuera de la imagen. El frontend
no recibe el `.env` del backend ni sus credenciales.

La red privada de Compose permite resolver `http://backend:8000` entre servicios.
Es una red bridge con salida: `internal: true` bloquearía Gemini y OCI. Los
puertos publicados usan solo `127.0.0.1`; HTTPS y la exposición externa mediante
Caddy corresponden al #48. Fuera del host no se publica directamente la API/UI.

Compose espera el health del backend y `frontend/entrypoint.py` comprueba además
activamente `/api/health` antes de iniciar Streamlit, con un plazo de 60 segundos.
Si se vence, el proceso termina con error y la política `unless-stopped` permite
un nuevo arranque. Esto no reemplaza el manejo de caídas durante uso del futuro
cliente HTTP del #16.

Ambas imágenes usan UID/GID 10001, código sin escritura, filesystem de solo
lectura, `/tmp` temporal y sin capabilities. El volumen nuevo hereda el dueño
no-root definido en la imagen. El backend incluye DejaVu Sans y bibliotecas
FreeType/JPEG/PNG/OpenJPEG para renderizado; el exportador PDF pertenece al #44.
Los contextos `.dockerignore` admiten solamente código/configuración necesaria
y requirements; excluyen secretos, tests, cachés, bases y subidas.

`docker compose down --volumes` **borra los datos del proyecto seleccionado**;
usalo solamente para un entorno efímero. Recrear contenedores no borra el
volumen; perder el disco sí requiere recuperación desde OCI (#47/#49).

## Construcción ARM64 y verificación repetible

Las dos imágenes fijan el índice multiarch oficial de Python 3.11.17 slim
Bookworm por digest. Sus dependencias Python directas mantienen versiones `==`
y se instalan antes del código para reutilizar la capa de dependencias.

```powershell
docker buildx build --platform linux/arm64 --load --tag nuevamente-backend:arm64 ./backend
docker buildx build --platform linux/arm64 --load --tag nuevamente-frontend:arm64 ./frontend
docker run --rm --platform linux/arm64 --entrypoint python nuevamente-backend:arm64 -c "import os, platform; print(platform.machine(), os.getuid())"
```

Docker Desktop proporciona emulación; en un host Linux x86 hace falta QEMU.
El workflow `.github/workflows/containers.yml` construye ambas imágenes ARM64,
las ejecuta bajo QEMU y verifica arquitectura/UID, sin publicar imágenes.
Otro job levanta Compose con mocks y ejecuta `.github/scripts/smoke_compose.py`.

Para reproducir el smoke en un proyecto **efímero**, sin utilizar tu volumen habitual:

```powershell
$env:COMPOSE_PROJECT_NAME = 'nuevamente-smoke'
$env:NUEVAMENTE_ENV_FILE = '.env.docker.example'
docker compose up --build --detach --wait --wait-timeout 120
python .github/scripts/smoke_compose.py
docker compose down --volumes
```

El smoke crea un espacio, recrea ambos contenedores y lo recupera por código:
acredita persistencia del SQLite real. Un marcador en el directorio de índices
acredita que también se conserva esa ruta del volumen. La integración ChromaDB
se implementa en el #17 y debe sumar sus propias pruebas reales del índice.

Referencias oficiales: [orden y health de Compose](https://docs.docker.com/compose/how-tos/startup-order/),
[construcción multiarch](https://docs.docker.com/build/building/multi-platform/)
y [redes de Compose](https://docs.docker.com/reference/compose-file/networks/).
