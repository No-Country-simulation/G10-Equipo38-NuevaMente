"""Registro operativo SQLite (issue #08, referencia §7.2, §7.3, §14.2).

Qué es este archivo: la capa de PERSISTENCIA OPERATIVA del backend. Toda la
información "viva" del servicio — espacios, sesiones, documentos, trabajos
de generación, claves de idempotencia y eventos de progreso — vive acá, en
UN archivo SQLite dentro del volumen del backend (``{DATA_DIR}``, montado
por Compose como ``backend_data:/app/.data``).

Por qué SQLite y no otra cosa (§7.2): es un archivo local que sobrevive a
reinicios del proceso y a recreaciones del contenedor (§14.2), corre en el
mismo proceso del backend sin servidor adicional (una sola VM, sin Redis ni
Celery), y su fiabilidad es sobrada para esta escala. OCI sigue siendo la
persistencia de los CONTENIDOS (originales y paquetes); este registro es
solo el estado operativo. SQLite no reemplaza a OCI: lo complementa.

Decisiones de diseño que el issue manda y este código cumple:

1. UN ÚNICO PROCESO ESCRITOR (§14.2): una sola conexión + un lock de
   escritura. WAL (Write-Ahead Logging) permite que las lecturas no bloqueen
   al escritor y viceversa, pero JAMÁS se arranca un segundo proceso
   escribiendo el mismo archivo sin rediseñar la coordinación.
2. SIN ORM: SQL directo con el módulo stdlib ``sqlite3``. Un ORM "pesado"
   no aporta aquí (pocas tablas, consultas simples) y sí agrega una
   dependencia y magia difícil de auditar.
3. MIGRACIONES VERSIONADAS: la tabla ``schema_migrations`` recuerda qué
   versión del esquema tiene la base; al abrir, se aplican solo las
   migraciones nuevas, en orden, cada una en una transacción. Así el
   esquema evoluciona sin borrar el estado de los usuarios.
4. NUNCA SECRETOS EN CLARO (§7.4 y §11.2): de los códigos de recuperación
   y de los tokens de sesión se guarda SOLO su hash (SHA-256). Si alguien
   roba el archivo de la base, no puede entrar a ningún espacio. La regla
   vale también para las respuestas cacheadas de idempotencia.
5. RECUPERACIÓN TRAS REINICIO (§7.2): al abrir la base, los trabajos que
   quedaron ``running`` (porque el proceso murió a mitad) se marcan
   ``failed`` con error ``INTERRUPTED``. Un trabajo interrumpido jamás
   aparece como completado: la UI nunca le prometerá al usuario algo que
   no pasó.
6. TOMBSTONES DE BORRADO (§8.5, §11.5): borrar un recurso agenda una
   "lápida" por el plazo de retención. Mientras la lápida viva, el recurso
   no vuelve a aparecer ni puede "resucitarse" aunque una reconstrucción
   desde OCI traiga sus manifiestos.

Si se pierde el volumen, session/recovery.py restaura el registro desde
manifiestos privados confirmados sin recuperar sesiones. La sincronización
de actividad y lápidas pertenece a session/manifests.py; este store
conserva las marcas pendientes y no llama a OCI.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from app.schemas.documents import CoberturaDocumento, MetadatosDocumento, ResultadoIngestionDocumental
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode

# Versión actual del esquema. Cada cambio de esquema agrega una entrada a
# MIGRACIONES y sube este número; NUNCA se edita una migración ya aplicada
# (las bases reales de los usuarios quedaron con la vieja).
VERSION_ESQUEMA = 12

# Cada migración: (versión, SQL). Se aplican en orden ascendente dentro de
# una transacción cada una. La v1 crea todas las tablas del issue #08.
MIGRACIONES: list[tuple[int, str]] = [
    (
        1,
        """
        -- Espacios anónimos (§7.4): el código de recuperación SOLO como hash;
        -- borrado_en actúa como lápida del espacio completo. version_manifiesto
        -- acompaña el manifiesto OCI del espacio (workspaces/{id}/manifest.json,
        -- 8.3): lo usa #9 para el control de versión entre pestañas y la
        -- sincronización de reconstrucción (7.2), NO como contador interno.
        CREATE TABLE workspaces (
            workspace_id   TEXT PRIMARY KEY,
            codigo_hash    TEXT NOT NULL,
            codigo_creado_en TEXT NOT NULL,
            expira_en      TEXT NOT NULL,
            borrado_en     TEXT,
            version_manifiesto INTEGER NOT NULL DEFAULT 1
        );

        -- Sesiones (§7.4): solo el hash del token (256 bits); revocables.
        CREATE TABLE sessions (
            token_hash    TEXT PRIMARY KEY,
            workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id),
            creada_en     TEXT NOT NULL,
            expira_en     TEXT NOT NULL,
            revocada_en   TEXT
        );

        -- Documentos cargados: estado según DocumentStatus del contrato (#03).
        CREATE TABLE documents (
            document_id  TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
            titulo       TEXT NOT NULL,
            hash         TEXT,
            estado       TEXT NOT NULL CHECK (estado IN ('processing', 'ready', 'failed')),
            detalle_error TEXT,
            creado_en    TEXT NOT NULL
        );

        -- Trabajos de generación: status según JobStatus del contrato (#03).
        -- error_code guarda códigos ESTABLES del contrato (p. ej. INTERRUPTED).
        CREATE TABLE generations (
            generation_id TEXT PRIMARY KEY,
            workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id),
            document_id   TEXT NOT NULL REFERENCES documents(document_id),
            status        TEXT NOT NULL CHECK (status IN (
                'queued', 'running', 'completed', 'rejected_quality', 'failed', 'cancelled')),
            error_code    TEXT,
            error_message TEXT,
            creado_en     TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        );

        -- Idempotencia (§7.3): atómica por la TERNA workspace+operación+clave.
        -- hash_cuerpo: SHA-256 del cuerpo de la petición; si cambia con la
        -- misma clave, es 409 IDEMPOTENCY_CONFLICT. recurso_id: el identificador
        -- que se devolvió la primera vez (mismo id en reintentos).
        -- respuesta_json: SOLO datos no sensibles (regla del issue: jamás
        -- códigos de recuperación ni tokens en claro).
        CREATE TABLE idempotency_keys (
            workspace_id TEXT NOT NULL,
            operacion    TEXT NOT NULL,
            clave        TEXT NOT NULL,
            hash_cuerpo  TEXT NOT NULL,
            recurso_id   TEXT,
            estado       TEXT NOT NULL CHECK (estado IN ('en_curso', 'completada')),
            respuesta_json TEXT,
            creado_en    TEXT NOT NULL,
            PRIMARY KEY (workspace_id, operacion, clave)
        );

        -- Eventos de progreso (§3.3): id AUTOINCREMENT = monotónico global,
        -- que es lo que SSE usa para Last-Event-ID al reconectar.
        CREATE TABLE events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id  TEXT NOT NULL,
            generation_id TEXT,
            job_id        TEXT,
            step          TEXT NOT NULL,
            status        TEXT NOT NULL,
            iteration     INTEGER,
            creado_en     TEXT NOT NULL
        );

        -- Tombstones de borrado (§8.5): "este recurso fue retirado por el
        -- usuario". purga_despues_en habilita limpieza, nunca acceso; existen para
        -- que una reconstrucción desde OCI no lo resucite (§11.5).
        CREATE TABLE pending_deletes (
            recurso_tipo    TEXT NOT NULL CHECK (recurso_tipo IN ('workspace', 'document', 'generation')),
            recurso_id      TEXT NOT NULL,
            workspace_id    TEXT NOT NULL,
            solicitado_en   TEXT NOT NULL,
            purga_despues_en TEXT NOT NULL,
            PRIMARY KEY (recurso_tipo, recurso_id)
        );

        CREATE INDEX idx_sessions_workspace ON sessions(workspace_id);
        CREATE INDEX idx_generations_workspace ON generations(workspace_id);
        CREATE INDEX idx_events_generation ON events(generation_id, id);
        """,
    ),
    (
        2,
        """
        -- Trabajos comunes (issue #20): ingestion, chat y glosario usan el
        -- MISMO gestor y la misma cola que las generaciones (contratos-api.md);
        -- status reutiliza los valores JobStatus del contrato (#03) y
        -- error_code guarda codigos estables (INTERRUPTED, DEADLINE, ...).
        CREATE TABLE jobs (
            job_id      TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            tipo        TEXT NOT NULL,
            status      TEXT NOT NULL CHECK (status IN (
                'queued', 'running', 'completed', 'rejected_quality', 'failed', 'cancelled')),
            error_code    TEXT,
            error_message TEXT,
            resultado     TEXT,
            intentos      INTEGER NOT NULL DEFAULT 0,
            creado_en     TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        );

        CREATE INDEX idx_jobs_workspace ON jobs(workspace_id);
        CREATE INDEX idx_jobs_cola ON jobs(status, creado_en);
        """,
    ),
    (
        3,
        """
        ALTER TABLE jobs ADD COLUMN generation_id TEXT REFERENCES generations(generation_id);
        CREATE UNIQUE INDEX idx_jobs_generation ON jobs(generation_id);
        CREATE INDEX idx_events_job ON events(job_id, id);
    """,
    ),
    (
        4,
        """
        -- Índice para optimizar búsquedas por hash de recuperación en resolver_workspace (§7.4, Issue 09)
        CREATE INDEX idx_workspaces_codigo_hash ON workspaces(codigo_hash);
        """,
    ),
    (
        5,
        """
        ALTER TABLE workspaces ADD COLUMN ultima_actividad_en TEXT;
        ALTER TABLE workspaces ADD COLUMN manifest_pendiente INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE pending_deletes ADD COLUMN manifest_confirmado_en TEXT;
        CREATE TABLE workspace_progress (
            workspace_id TEXT PRIMARY KEY REFERENCES workspaces(workspace_id),
            version INTEGER NOT NULL CHECK (version > 0),
            state_json TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        );
        CREATE TABLE recovery_state (id INTEGER PRIMARY KEY CHECK (id = 1), pendiente INTEGER NOT NULL);
        INSERT INTO recovery_state VALUES (1, CASE WHEN EXISTS(SELECT 1 FROM workspaces) THEN 0 ELSE 1 END);
        """,
    ),
    (
        6,
        """
        ALTER TABLE documents ADD COLUMN source_name TEXT;
        ALTER TABLE documents ADD COLUMN metadatos_json TEXT;
        ALTER TABLE documents ADD COLUMN cobertura_json TEXT;
        ALTER TABLE jobs ADD COLUMN document_id TEXT REFERENCES documents(document_id);
        CREATE INDEX idx_jobs_document ON jobs(document_id);
        CREATE TABLE document_admissions (
            document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
            workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
            job_id TEXT NOT NULL UNIQUE,
            clave_idempotencia TEXT,
            estado TEXT NOT NULL CHECK (estado IN ('en_curso', 'encolada', 'fallida')),
            error_code TEXT,
            actualizado_en TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_admission_workspace ON document_admissions(workspace_id)
            WHERE estado = 'en_curso';
        CREATE TABLE document_cleanup (
            document_id TEXT PRIMARY KEY REFERENCES documents(document_id),
            workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
            estado TEXT NOT NULL CHECK (estado IN ('reservada', 'pendiente', 'completada')),
            solicitado_en TEXT NOT NULL,
            intentos INTEGER NOT NULL DEFAULT 0 CHECK (intentos >= 0),
            ultimo_error TEXT,
            proximo_intento_en TEXT,
            completado_en TEXT
        );
        CREATE INDEX idx_document_cleanup ON document_cleanup(estado, proximo_intento_en, document_id);
        """,
    ),
    (
        7,
        """
        ALTER TABLE document_admissions ADD COLUMN escritura_en_curso INTEGER NOT NULL DEFAULT 0
            CHECK (escritura_en_curso IN (0, 1));
        """,
    ),
    (
        8,
        """
        ALTER TABLE documents ADD COLUMN recuperacion_evidencia TEXT;
        ALTER TABLE documents ADD COLUMN recuperacion_estado TEXT
            CHECK (recuperacion_estado IN ('pendiente','reservada','en_curso','confirmada','fallida'));
        ALTER TABLE documents ADD COLUMN recuperacion_job_id TEXT;
        ALTER TABLE documents ADD COLUMN recuperacion_intentos INTEGER NOT NULL DEFAULT 0
            CHECK (recuperacion_intentos BETWEEN 0 AND 2);
        CREATE UNIQUE INDEX idx_document_recovery_job ON documents(recuperacion_job_id)
            WHERE recuperacion_job_id IS NOT NULL;
        """,
    ),
]


MIGRACIONES.append(
    (
        9,
        """
        ALTER TABLE generations ADD COLUMN parametros_json TEXT;
        DROP INDEX idx_jobs_generation;
        CREATE INDEX idx_jobs_generation ON jobs(generation_id);
        CREATE UNIQUE INDEX idx_jobs_generation_active ON jobs(generation_id)
            WHERE generation_id IS NOT NULL AND status IN ('queued', 'running');
        CREATE TABLE approved_outputs (
            generation_id TEXT PRIMARY KEY REFERENCES generations(generation_id) ON DELETE CASCADE,
            paquete_json TEXT NOT NULL,
            expira_en TEXT NOT NULL
        );
        CREATE INDEX idx_generations_page ON generations(workspace_id, creado_en, generation_id);
        """,
    )
)


MIGRACIONES.append(
    (
        10,
        """
    ALTER TABLE jobs ADD COLUMN cache_key TEXT;
    CREATE INDEX idx_jobs_glossary_cache ON jobs(workspace_id, cache_key, creado_en)
        WHERE tipo='glosario';
""",
    )
)


MIGRACIONES.append(
    (
        11,
        """
CREATE TABLE workspace_cleanup (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(workspace_id),
    estado TEXT NOT NULL DEFAULT 'pendiente', intentos INTEGER NOT NULL DEFAULT 0,
    error_code TEXT, actualizado_en TEXT
);
""",
    )
)


MIGRACIONES.append(
    (
        12,
        """
CREATE TABLE generation_documents (
    generation_id TEXT NOT NULL REFERENCES generations(generation_id) ON DELETE CASCADE,
    document_id   TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    titulo        TEXT,
    hash          TEXT,
    version       TEXT,
    PRIMARY KEY (generation_id, document_id)
);
CREATE INDEX idx_gen_docs_doc ON generation_documents(document_id);
CREATE INDEX idx_gen_docs_gen ON generation_documents(generation_id);

CREATE TABLE job_documents (
    job_id       TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, document_id)
);
CREATE INDEX idx_job_docs_doc ON job_documents(document_id);
CREATE INDEX idx_job_docs_job ON job_documents(job_id);

ALTER TABLE generations ADD COLUMN snapshot_fuentes_json TEXT;
ALTER TABLE jobs ADD COLUMN scope_hash TEXT;
CREATE INDEX idx_jobs_scope_hash ON jobs(workspace_id, scope_hash) WHERE scope_hash IS NOT NULL;

INSERT INTO generation_documents (generation_id, document_id, titulo, hash, version)
SELECT g.generation_id, g.document_id, d.titulo, d.hash, 'v1'
FROM generations g
JOIN documents d ON g.document_id = d.document_id;

INSERT INTO job_documents (job_id, document_id)
SELECT j.job_id, j.document_id
FROM jobs j
JOIN documents d ON j.document_id = d.document_id
WHERE j.document_id IS NOT NULL;

INSERT OR IGNORE INTO job_documents (job_id, document_id)
SELECT j.job_id, gd.document_id
FROM jobs j
JOIN generation_documents gd ON j.generation_id = gd.generation_id;

UPDATE jobs SET scope_hash = json_extract(resultado, '$.document_hash')
WHERE tipo = 'chat' AND resultado IS NOT NULL AND json_extract(resultado, '$.document_hash') IS NOT NULL;
""",
    )
)


def retirar_referencias(valor, document_id: str, generaciones: set[str]):
    """Retira únicamente registros que declaran una relación conocida."""
    if isinstance(valor, dict):
        if valor.get("document_id") == document_id or (
            isinstance(valor.get("generation_id"), str) and valor["generation_id"] in generaciones
        ):
            return None
        return {
            clave: filtrado
            for clave, item in valor.items()
            if (filtrado := retirar_referencias(item, document_id, generaciones)) is not None or item is None
        }
    if isinstance(valor, list):
        return [
            item for original in valor if (item := retirar_referencias(original, document_id, generaciones)) is not None
        ]
    return valor


class EspacioDocumentalOcupado(ValueError):
    """Límite operativo; distinto de un estado documental incompatible."""


class ConflictoIdempotenciaDocumento(ValueError):
    """La clave de upload ya pertenece a otro cuerpo."""


def _ahora() -> datetime:
    """Reloj UTC en zona explícita: TODAS las fechas de la base son ISO-8601 UTC."""
    return datetime.now(timezone.utc)


def _iso(momento: datetime) -> str:
    return momento.isoformat()


def _hash_de(secreto: str) -> str:
    """Hash SHA-256 de un código/token: lo ÚNICO que esta base guarda de ellos.

    Un hash es "de ida": permite verificar que un código presentado es el
    correcto (hasheando y comparando), pero de él NO se puede volver al
    código. Es exactamente la regla de §7.4 ("el servidor conserva solo su
    hash; nunca el código en claro").
    """
    return hashlib.sha256(secreto.encode("utf-8")).hexdigest()


def _sincronizado(metodo):
    """Serializa también las lecturas de la conexión compartida entre hilos."""

    @wraps(metodo)
    def ejecutar(self, *args, **kwargs):
        with self._lock:
            return metodo(self, *args, **kwargs)

    return ejecutar


def _validar_respuesta_cacheada(valor) -> None:
    """Impide persistir credenciales de sesión/recuperación en el caché de respuestas."""
    if isinstance(valor, dict):
        for clave, contenido in valor.items():
            if str(clave).casefold() in {
                "recovery_code",
                "codigo_recuperacion",
                "token",
                "session_token",
                "access_token",
                "authorization",
            }:
                raise ValueError("No se pueden cachear credenciales en claro")
            _validar_respuesta_cacheada(contenido)
    elif isinstance(valor, list):
        for contenido in valor:
            _validar_respuesta_cacheada(contenido)


class RegistroOperativo:
    """Acceso al registro operativo. Una instancia = UNA conexión escritora.

    Para tests y para la fábrica del app: la ruta de la base y el plazo de
    retención se pasan por parámetro (el default de producción sale de la
    configuración: DATA_DIR + retención de 30 días del Apéndice A).

    Thread-safety: el lock serializa lecturas y escrituras sobre la conexión
    compartida (FastAPI atiende pedidos en varios hilos). WAL no aísla
    transacciones entre hilos de esa misma conexión. EN UN SOLO PROCESO: dos procesos
    sobre la misma base violan el modelo de §14.2 y no está soportado.
    """

    def __init__(
        self,
        ruta_db: str | Path,
        dias_retencion: int = 30,
        limite_documentos: int = 5,
        limite_generaciones: int = 20,
        *,
        max_documentos: int | None = None,
        max_generaciones: int | None = None,
    ) -> None:
        self.ruta_db = Path(ruta_db)
        self.ruta_db.parent.mkdir(parents=True, exist_ok=True)
        self.dias_retencion = dias_retencion
        self.limite_documentos = max_documentos if max_documentos is not None else limite_documentos
        self.limite_generaciones = max_generaciones if max_generaciones is not None else limite_generaciones
        if (
            type(self.limite_documentos) is not int
            or type(self.limite_generaciones) is not int
            or self.limite_documentos <= 0
            or self.limite_generaciones <= 0
        ):
            raise ValueError("Los límites de recursos del espacio deben ser enteros positivos.")
        self.max_documentos = self.limite_documentos
        self.max_generaciones = self.limite_generaciones
        self._lock = threading.RLock()
        # check_same_thread=False: la conexión se usa desde los hilos del
        # threadpool de FastAPI (siempre bajo el lock de arriba).
        self._conn = sqlite3.connect(str(self.ruta_db), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # WAL: las escrituras van a un archivo lateral (-wal) y las lecturas
        # no se bloquean. Es el modo que recomienda §14.2 para escritor único
        # con lecturas concurrentes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        # foreign_keys: SQLite viene con integridad referencial APAGADA por
        # defecto; sin esto, los REFERENCES de arriba serían decoración.
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Si otro lector demora, esperar hasta 5 s antes de fallar con "locked".
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._aplicar_migraciones()
        # Recuperación al arrancar (§7.2): parte del ciclo de vida de la base.
        self.recuperar_tras_reinicio()

    # ------------------------------------------------------------------
    # Migraciones y recuperación
    # ------------------------------------------------------------------

    def _aplicar_migraciones(self) -> None:
        """Aplica las migraciones que falten, en orden, una transacción cada una."""
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, aplicada_en TEXT NOT NULL)"
            )
            aplicadas = {fila["version"] for fila in self._conn.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRACIONES:
                if version in aplicadas:
                    continue
                # ATOMICIDAD: executescript() de sqlite3 hace COMMIT implícito
                # de cualquier transacción abierta antes de ejecutar, así que
                # el BEGIN/COMMIT se incluye DENTRO del script, junto al
                # registro de versión: o se aplica la migración completa (DDL
                # + marca), o no se aplica nada y la base queda como estaba.
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + f"\nINSERT INTO schema_migrations (version, aplicada_en) VALUES ({int(version)}, '{_iso(_ahora())}');\nCOMMIT;"
                )
                try:
                    self._conn.executescript(script)
                except Exception:
                    # Si el script falló a mitad, su transacción quedó abierta:
                    # revertir para no dejar DDL a medias en la base.
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise

    @_sincronizado
    def version_esquema(self) -> int:
        """Versión de esquema vigente en la base (para diagnóstico y tests)."""
        fila = self._conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
        return int(fila["v"])

    def recuperar_tras_reinicio(self) -> int:
        """Marca como fallidos los trabajos que quedaron a medias (§7.2 y §14.2).

        Cuando el proceso muere (corte, crash, recreación del contenedor), un
        trabajo ``running`` se quedó SIN quien lo esté ejecutando. Dejarlo
        así sería mentirle a la UI para siempre. Al abrir la base, todo
        ``running`` pasa a ``failed`` con error_code=INTERRUPTED: el usuario
        verá la verdad y podrá regenerar. Devuelve cuántos marcó (útil para
        el log de arranque, §11.5).

        Los ``queued`` NO se tocan acá: reencolarlos o cancelarlos es
        decisión del gestor de la cola (issue #20), que conoce la política
        de reintentos.
        """
        with self._lock:
            self._conn.execute("UPDATE document_admissions SET escritura_en_curso=0")
            cursor = self._conn.execute(
                """
                UPDATE generations
                   SET status = ?, error_code = 'INTERRUPTED',
                       error_message = 'El proceso se interrumpio mientras el trabajo corria.',
                       actualizado_en = ?
                 WHERE status = ?
                """,
                (JobStatus.FAILED.value, _iso(_ahora()), JobStatus.RUNNING.value),
            )
            # Las admisiones cuyo proceso murió conservan IDs y evidencia para limpieza.
            for fila in self._conn.execute(
                "SELECT document_id FROM document_admissions WHERE estado='en_curso'"
            ).fetchall():
                self.fallar_admision_documental(fila["document_id"], "INTERRUPTED", "La admisión se interrumpió.")
            return cursor.rowcount

    @contextmanager
    def transaccion(self):
        """
        Context manager para agrupar operaciones atómicas en SQLite.
        Mantiene el lock de escrituras y maneja BEGIN/COMMIT/ROLLBACK.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def crear_workspace(self, workspace_id: str, codigo_recuperacion: str, dias_validez: int) -> tuple[str, str]:
        """Registra un espacio nuevo; del código se guarda SOLO el hash (§7.4).
        Devuelve el ID del workspace y la fecha de expiración.
        """
        ahora = _ahora()
        expira_en = _iso(ahora + timedelta(days=dias_validez))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO workspaces (workspace_id, codigo_hash, codigo_creado_en, expira_en, ultima_actividad_en)
                VALUES (?, ?, ?, ?, ?)
                """,
                (workspace_id, _hash_de(codigo_recuperacion), _iso(ahora), expira_en, _iso(ahora)),
            )
        return (workspace_id, expira_en)

    @_sincronizado
    def obtener_workspace(self, workspace_id: str) -> dict | None:
        """Datos del espacio, o None si no existe o fue borrado (lápida)."""
        fila = self._conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        if fila is None or fila["borrado_en"] is not None or datetime.fromisoformat(fila["expira_en"]) <= _ahora():
            return None
        return dict(fila)

    @_sincronizado
    def resolver_workspace(self, codigo_recuperacion: str) -> dict | None:
        """Datos del espacio, o None si no existe o fue borrado (lápida)."""
        fila = self._conn.execute(
            "SELECT * FROM workspaces WHERE codigo_hash = ?", (_hash_de(codigo_recuperacion),)
        ).fetchone()
        if fila is None or fila["borrado_en"] is not None or datetime.fromisoformat(fila["expira_en"]) <= _ahora():
            return None
        return dict(fila)

    def borrar_workspace(self, workspace_id: str) -> None:
        """Borrado de espacio completo (DELETE /api/workspaces/current, §7.1 y §8.5).

        UNA operación atómica que hace las tres cosas que el borrado exige:

        1. Marca `borrado_en` en el espacio: TODAS las lecturas que filtran
           por espacio dejan de servirlo al instante ("el acceso queda
           bloqueado aunque la limpieza física tarde", §8.5).
        2. Revoca todas sus sesiones (quien estaba dentro, queda afuera).
        3. Agenda la lápida del espacio: una reconstrucción futura desde
           manifiestos OCI no puede resucitar lo que el usuario retiró
           (§11.5).
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """UPDATE workspaces SET borrado_en = ?, manifest_pendiente = 1,
                       version_manifiesto = version_manifiesto + 1
                       WHERE workspace_id = ? AND borrado_en IS NULL""",
                    (_iso(_ahora()), workspace_id),
                )
                self.revocar_sesiones_de_workspace(workspace_id)
                self.agendar_borrado("workspace", workspace_id, workspace_id)
                self._conn.execute("INSERT OR IGNORE INTO workspace_cleanup(workspace_id) VALUES (?)", (workspace_id,))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def registrar_rotacion(
        self,
        workspace_id: str,
        nuevo_codigo_recuperacion: str,
        nuevo_token: str,
        nueva_version: int,
        dias_validez_ws: int,
        horas_validez_sesion: int,
    ) -> tuple[str, str]:
        """
        Primeramente verifica si ya existe una transaccion activa, en dicho caso simplemente ejecuta
        el método directamente sin necesidad de abrir una nueva transacción, en caso contrario abre la
        transacción y procede a ejecutar la rotación de credenciales mediante el método privado `__ejecutar_rotacion_transaccional`.
        Devuelve el ID del workspace y la nueva fecha de expiración.
        """
        with self._lock:
            if self._conn.in_transaction:
                return self.__ejecutar_rotacion_transaccional(
                    workspace_id,
                    nuevo_codigo_recuperacion,
                    nuevo_token,
                    nueva_version,
                    dias_validez_ws,
                    horas_validez_sesion,
                )

            with self.transaccion():
                return self.__ejecutar_rotacion_transaccional(
                    workspace_id,
                    nuevo_codigo_recuperacion,
                    nuevo_token,
                    nueva_version,
                    dias_validez_ws,
                    horas_validez_sesion,
                )

    def __ejecutar_rotacion_transaccional(
        self,
        workspace_id: str,
        nuevo_codigo_recuperacion: str,
        nuevo_token: str,
        nueva_version: int,
        dias_validez_ws: int,
        horas_validez_sesion: int,
    ) -> tuple[str, str]:
        """
        Ejecuta la rotación completa de credenciales en una operación atómica.
        Invalida el código anterior, revoca sesiones y guarda los nuevos accesos.
        Devuelve el ID del workspace y la nueva fecha de expiración.
        """
        # 1. Revocar TODAS las sesiones previas del espacio
        self.revocar_sesiones_de_workspace(workspace_id)

        expira_en = _iso(_ahora() + timedelta(days=dias_validez_ws))

        # 2. Actualizar el código, renovar expiración e incrementar versión
        cursor = self._conn.execute(
            """
            UPDATE workspaces 
            SET codigo_hash = ?, 
                expira_en = ?, 
                version_manifiesto = ?, ultima_actividad_en = ?
            WHERE workspace_id = ? AND version_manifiesto = ?
                AND borrado_en IS NULL AND expira_en > ?
            """,
            (
                _hash_de(nuevo_codigo_recuperacion),
                expira_en,
                nueva_version,
                _iso(_ahora()),
                workspace_id,
                nueva_version - 1,
                _iso(_ahora()),
            ),
        )

        if cursor.rowcount == 0:
            raise ValueError("El espacio de trabajo fue modificado desde otra sesión o ya no está disponible")

        # 3. Registrar el hash de la nueva sesión generada
        self.crear_sesion(nuevo_token, workspace_id, horas_validez_sesion)

        return (workspace_id, expira_en)

    def renovar_expiracion_workspace(self, workspace_id: str, dias_validez: int, nueva_version: int) -> tuple[str, str]:
        """
        Renueva la fecha de expiración del workspace. Devuelve la fecha ISO exacta guardada en SQLite.
        Devuelve el ID del workspace y la nueva fecha de expiración.
        """
        ahora = _ahora()
        expira_en = _iso(ahora + timedelta(days=dias_validez))

        cursor = self._conn.execute(
            """
                UPDATE workspaces
                SET expira_en = ?, version_manifiesto = ?, ultima_actividad_en = ?
                WHERE workspace_id = ? AND borrado_en IS NULL AND expira_en > ?
                AND version_manifiesto = ? 
            """,
            (expira_en, nueva_version, _iso(ahora), workspace_id, _iso(ahora), nueva_version - 1),
        )

        if cursor.rowcount == 0:
            raise ValueError("El espacio de trabajo fue modificado desde otra sesión o ya no está disponible")

        return workspace_id, expira_en

    # ------------------------------------------------------------------
    # Sesiones (§7.4)
    # ------------------------------------------------------------------

    def crear_sesion(self, token: str, workspace_id: str, horas_validez: int) -> None:
        """Registra una sesión; del token SOLO el hash. SESSION_MAX_HOURS la fija el llamador."""
        ahora = _ahora()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (token_hash, workspace_id, creada_en, expira_en)
                VALUES (?, ?, ?, ?)
                """,
                (_hash_de(token), workspace_id, _iso(ahora), _iso(ahora + timedelta(hours=horas_validez))),
            )

    @_sincronizado
    def obtener_sesion(self, token: str) -> dict | None:
        """Resuelve un token a su sesión SOLO si es plenamente válida.

        Una sesión sirve si y solo si: existe (el hash coincide), no está
        revocada, no expiró (SESSION_MAX_HOURS) y su espacio NO fue borrado.
        Cualquier otra cosa devuelve None: la API lo traduce a 401
        SESSION_INVALID. Bloquear sesiones de espacios borrados es lo que
        hace el borrado "inmediato" aunque la limpieza física tarde (§8.5).
        """
        fila = self._conn.execute(
            """
            SELECT s.*, w.borrado_en AS workspace_borrado_en, w.expira_en AS workspace_expira_en
              FROM sessions s
              JOIN workspaces w ON w.workspace_id = s.workspace_id
             WHERE s.token_hash = ?
            """,
            (_hash_de(token),),
        ).fetchone()
        if fila is None or fila["revocada_en"] is not None or fila["workspace_borrado_en"] is not None:
            return None
        if min(datetime.fromisoformat(fila[c]) for c in ("expira_en", "workspace_expira_en")) <= _ahora():
            return None
        return dict(fila)

    def revocar_sesion(self, token: str) -> None:
        """Cierra la sesión actual (DELETE /api/sessions/current, §7.1)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET revocada_en = ? WHERE token_hash = ? AND revocada_en IS NULL",
                (_iso(_ahora()), _hash_de(token)),
            )

    def revocar_sesiones_de_workspace(self, workspace_id: str) -> int:
        """Revoca TODAS las sesiones del espacio (rotación de código y borrado, §7.4)."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET revocada_en = ? WHERE workspace_id = ? AND revocada_en IS NULL",
                (_iso(_ahora()), workspace_id),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Documentos
    # ------------------------------------------------------------------

    def registrar_documento(
        self,
        document_id: str,
        workspace_id: str,
        titulo: str,
        estado: DocumentStatus,
        hash_documento: str | None = None,
        *,
        source_name: str | None = None,
        metadatos: MetadatosDocumento | None = None,
        cobertura: CoberturaDocumento | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO documents (document_id, workspace_id, titulo, hash, estado, creado_en,
                    source_name, metadatos_json, cobertura_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    workspace_id,
                    titulo,
                    hash_documento,
                    estado.value,
                    _iso(_ahora()),
                    source_name,
                    metadatos.model_dump_json() if metadatos else None,
                    cobertura.model_dump_json() if cobertura else None,
                ),
            )

    @_sincronizado
    def acreditar_documento_restaurado(self, document_id: str, evidencia: dict) -> None:
        """Procedencia validada por recovery.py; no recrea una admisión histórica."""
        from app.core.rag.rebuild import ManifiestoDocumento

        manifest = ManifiestoDocumento.model_validate_json(json.dumps(evidencia["manifest"]))
        doc = self.obtener_documento(document_id)
        if (
            not doc
            or (manifest.workspace_id, manifest.document_id, manifest.hash_sha256)
            != (doc["workspace_id"], document_id, doc["hash"])
            or manifest.borrado
            or manifest.admision_confirmada is not True
            or manifest.admision_sqlite_confirmada is not True
            or manifest.metadatos is None
            or not evidencia.get("etag")
            or self.obtener_admision_documental(doc["workspace_id"], document_id) is not None
        ):
            raise ValueError("Procedencia documental no verificable")
        self._conn.execute(
            """UPDATE documents SET recuperacion_evidencia=?, recuperacion_estado=?, creado_en=?, detalle_error=?
               WHERE document_id=? AND recuperacion_evidencia IS NULL""",
            (
                json.dumps(evidencia, ensure_ascii=False),
                "fallida" if manifest.estado == DocumentStatus.FAILED else "pendiente",
                manifest.metadatos.creado_en.isoformat(),
                manifest.detalle_error,
                document_id,
            ),
        )

    @_sincronizado
    def procedencia_documental_verificada(self, doc: dict) -> bool:
        from app.core.rag.rebuild import ManifiestoDocumento

        if not doc.get("recuperacion_evidencia"):
            return False
        evidencia = json.loads(doc["recuperacion_evidencia"])
        manifest = ManifiestoDocumento.model_validate_json(json.dumps(evidencia["manifest"]))
        return bool(
            evidencia.get("etag")
            and manifest.admision_confirmada is True
            and manifest.admision_sqlite_confirmada is True
            and not manifest.borrado
            and manifest.metadatos is not None
            and (manifest.workspace_id, manifest.document_id, manifest.hash_sha256, manifest.source_name)
            == (doc["workspace_id"], doc["document_id"], doc["hash"], doc["source_name"])
        )

    @_sincronizado
    def reconstrucciones_documentales_pendientes(self) -> list[dict]:
        return [
            dict(fila)
            for fila in self._conn.execute(
                """SELECT d.* FROM documents d LEFT JOIN jobs j ON j.job_id=d.recuperacion_job_id
                   WHERE d.recuperacion_evidencia IS NOT NULL AND d.recuperacion_intentos<2
                     AND (d.recuperacion_estado IN ('pendiente','reservada') OR
                          (d.recuperacion_estado='fallida' AND j.error_code='INTERRUPTED'))
                     AND NOT EXISTS (SELECT 1 FROM document_cleanup c WHERE c.document_id=d.document_id
                                     AND c.estado IN ('reservada','pendiente'))
                   ORDER BY d.creado_en, d.document_id"""
            ).fetchall()
            if self.obtener_documento(fila["document_id"]) is not None
        ]

    def registrar_indice_reconstruccion(self, document_id: str, job_id: str, previo: dict | None) -> None:
        """Obligación anterior al I/O Chroma; conserva un índice compatible previo."""
        with self.transaccion():
            doc = self.obtener_documento(document_id)
            if doc is None or doc["recuperacion_estado"] != "en_curso" or doc["recuperacion_job_id"] != job_id:
                raise ValueError("El intento de reconstrucción dejó de estar vigente")
            evidencia = json.loads(doc["recuperacion_evidencia"])
            evidencia["indice_intento"] = {"job_id": job_id, "previo": previo}
            self._conn.execute(
                "UPDATE documents SET recuperacion_evidencia=? WHERE document_id=?",
                (json.dumps(evidencia, ensure_ascii=False), document_id),
            )

    def reservar_reconstruccion_documental(self, document_id: str, job_id: str) -> dict | None:
        """Intención durable anterior al despacho; dos intentos reales como máximo."""
        with self.transaccion():
            doc = self.obtener_documento(document_id)
            if doc is None or not self.procedencia_documental_verificada(doc) or doc["recuperacion_intentos"] >= 2:
                return None
            if doc["recuperacion_estado"] == "reservada":
                return doc
            anterior = self.obtener_job(doc["recuperacion_job_id"]) if doc["recuperacion_job_id"] else None
            if doc["recuperacion_estado"] != "pendiente" and not (
                doc["recuperacion_estado"] == "fallida" and anterior and anterior["error_code"] == "INTERRUPTED"
            ):
                return None
            if self._conn.execute(
                "SELECT 1 FROM document_cleanup WHERE document_id=? AND estado!='completada'", (document_id,)
            ).fetchone():
                return None
            self._conn.execute(
                """UPDATE documents SET recuperacion_estado='reservada', recuperacion_job_id=?,
                   estado='processing', detalle_error=NULL WHERE document_id=?""",
                (job_id, document_id),
            )
            return self.obtener_documento(document_id)

    def actualizar_documento(
        self,
        document_id: str,
        estado: DocumentStatus,
        detalle_error: str | None = None,
        hash_documento: str | None = None,
    ) -> None:
        """Transición de estado del documento (§3.2 paso 6: processing→ready/failed)."""
        with self._lock:
            if hash_documento is not None:
                self._conn.execute(
                    "UPDATE documents SET estado = ?, detalle_error = ?, hash = ? WHERE document_id = ?",
                    (estado.value, detalle_error, hash_documento, document_id),
                )
            else:
                self._conn.execute(
                    "UPDATE documents SET estado = ?, detalle_error = ? WHERE document_id = ?",
                    (estado.value, detalle_error, document_id),
                )

    @_sincronizado
    def obtener_documento(self, document_id: str) -> dict | None:
        """Documento solo si existe y NADIE lo retiró: ni su espacio (lápida
        de workspace) ni él mismo (lápida de documento, §11.5)."""
        if self.esta_borrado("document", document_id):
            return None
        fila = self._conn.execute(
            """
            SELECT d.* FROM documents d
            JOIN workspaces w ON w.workspace_id = d.workspace_id
            WHERE d.document_id = ? AND w.borrado_en IS NULL AND w.expira_en > ?
            """,
            (document_id, _iso(_ahora())),
        ).fetchone()
        return dict(fila) if fila else None

    @_sincronizado
    def listar_documentos(self, workspace_id: str, *, limit: int = 20, cursor: str = "") -> list[dict]:
        """Página interna por ID; excluye reservas sin commit y recursos retirados.

        Storage y la sesión se verifican en el servicio, fuera del lock SQLite.
        El cursor interno no define todavía su representación HTTP.
        """
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("La página interna admite entre 1 y 101 filas")
        if not isinstance(cursor, str):
            raise ValueError("El cursor interno debe ser un ID")
        filas = self._conn.execute(
            """
            SELECT d.* FROM documents d
            JOIN workspaces w ON w.workspace_id=d.workspace_id
            LEFT JOIN document_admissions a ON a.document_id=d.document_id
            WHERE d.workspace_id=? AND d.document_id>?
              AND w.borrado_en IS NULL AND w.expira_en>?
              AND (a.document_id IS NULL OR a.estado='encolada')
              AND NOT EXISTS (SELECT 1 FROM pending_deletes r
                              WHERE (r.recurso_tipo='document' AND r.recurso_id=d.document_id)
                                 OR (r.recurso_tipo='workspace' AND r.recurso_id=d.workspace_id))
            ORDER BY d.document_id LIMIT ?
            """,
            (workspace_id, cursor, _iso(_ahora()), limit),
        ).fetchall()
        return [dict(fila) for fila in filas]

    @_sincronizado
    def pagina_documentos(
        self, workspace_id: str, *, despues: tuple[str, str] = ("", ""), limit: int = 101
    ) -> list[dict]:
        """Keyset por fecha e ID; el ancla puede haber sido retirada."""
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("La página admite entre 1 y 101 filas")
        filas = self._conn.execute(
            """
            SELECT d.* FROM documents d JOIN workspaces w ON w.workspace_id=d.workspace_id
            LEFT JOIN document_admissions a ON a.document_id=d.document_id
            WHERE d.workspace_id=? AND (d.creado_en, d.document_id)>(?, ?)
              AND w.borrado_en IS NULL AND w.expira_en>?
              AND (a.document_id IS NULL OR a.estado='encolada')
              AND NOT EXISTS (SELECT 1 FROM pending_deletes r
                  WHERE (r.recurso_tipo='document' AND r.recurso_id=d.document_id)
                     OR (r.recurso_tipo='workspace' AND r.recurso_id=d.workspace_id))
            ORDER BY d.creado_en, d.document_id LIMIT ?
            """,
            (workspace_id, *despues, _iso(_ahora()), limit),
        ).fetchall()
        return [dict(fila) for fila in filas]

    @_sincronizado
    def guardar_metadata_documental(
        self, workspace_id: str, document_id: str, *, metadatos: MetadatosDocumento, cobertura: CoberturaDocumento
    ) -> None:
        """Conserva cobertura/visión sin publicar ready ni permitir escrituras ajenas."""
        documento = self.obtener_documento(document_id)
        if documento is None or documento["workspace_id"] != workspace_id:
            raise ValueError("El documento no está disponible en este espacio")
        if documento["estado"] == DocumentStatus.READY.value and cobertura.paginas_visuales_pendientes:
            raise ValueError("Un documento ready no puede adquirir visión pendiente")
        self._conn.execute(
            "UPDATE documents SET metadatos_json=?, cobertura_json=? WHERE document_id=?",
            (metadatos.model_dump_json(), cobertura.model_dump_json(), document_id),
        )

    # ------------------------------------------------------------------
    # Admisión documental
    # ------------------------------------------------------------------

    def reservar_admision_documental(
        self,
        *,
        token: str,
        document_id: str,
        job_id: str,
        titulo: str,
        source_name: str,
        hash_documento: str,
        metadatos: MetadatosDocumento,
        cuerpo: str,
        clave: str | None,
    ) -> tuple[dict, bool]:
        with self.transaccion():
            sesion = self.obtener_sesion(token)
            if sesion is None:
                raise ValueError("La sesión no está disponible")
            workspace_id = sesion["workspace_id"]
            if clave is not None:
                resultado, previo = self.idempotencia_iniciar(workspace_id, "upload", clave, cuerpo, document_id)
                if resultado == "conflicto":
                    raise ConflictoIdempotenciaDocumento()
                if resultado == "duplicada":
                    registro = self.obtener_admision_documental(workspace_id, previo)
                    if registro is None:
                        raise ValueError("La admisión fue retirada o no está disponible")
                    return registro, False
            if (
                self.contar_activos_de_workspace(workspace_id)
                or self._conn.execute(
                    "SELECT 1 FROM document_admissions WHERE workspace_id=? AND estado='en_curso'", (workspace_id,)
                ).fetchone()
            ):
                raise EspacioDocumentalOcupado("El espacio ya tiene un trabajo o una admisión en curso")
            self.comprobar_limite_recursos(workspace_id, "documents", self.limite_documentos)
            self.registrar_documento(
                document_id,
                workspace_id,
                titulo,
                DocumentStatus.PROCESSING,
                hash_documento,
                source_name=source_name,
                metadatos=metadatos,
            )
            ahora = _iso(_ahora())
            self._conn.execute(
                """INSERT INTO document_admissions
                   (document_id, workspace_id, job_id, clave_idempotencia, estado, error_code, actualizado_en, escritura_en_curso)
                   VALUES (?, ?, ?, ?, 'en_curso', NULL, ?, 1)""",
                (document_id, workspace_id, job_id, clave, ahora),
            )
            self._conn.execute(
                "INSERT INTO document_cleanup (document_id, workspace_id, estado, solicitado_en) VALUES (?, ?, 'reservada', ?)",
                (document_id, workspace_id, ahora),
            )
            return self.obtener_admision_documental(workspace_id, document_id), True

    @_sincronizado
    def obtener_admision_documental(self, workspace_id: str, document_id: str) -> dict | None:
        documento = self.obtener_documento(document_id)
        if documento is None or documento["workspace_id"] != workspace_id:
            return None
        fila = self._conn.execute("SELECT * FROM document_admissions WHERE document_id=?", (document_id,)).fetchone()
        if fila is None:
            return None
        registro = {**dict(fila), "estado_documento": documento["estado"]}
        if registro["estado"] == "fallida" and registro["clave_idempotencia"] is not None:
            cache = self._conn.execute(
                "SELECT respuesta_json FROM idempotency_keys WHERE workspace_id=? AND operacion='upload' AND clave=?",
                (workspace_id, registro["clave_idempotencia"]),
            ).fetchone()
            respuesta = json.loads(cache["respuesta_json"]) if cache and cache["respuesta_json"] else None
            if respuesta and "error" in respuesta:
                registro["respuesta_error"] = respuesta["error"]
        return registro

    def confirmar_admision_documental(self, token: str, document_id: str) -> None:
        """Confirma el registro local; la aceptación exige además su evidencia en storage."""
        with self.transaccion():
            sesion = self.obtener_sesion(token)
            registro = self.obtener_admision_documental(sesion["workspace_id"], document_id) if sesion else None
            trabajo = self.obtener_job(registro["job_id"]) if registro else None
            if not registro or registro["estado"] != "en_curso" or registro["estado_documento"] != "processing":
                raise ValueError("La admisión no está disponible")
            if not trabajo or trabajo["document_id"] != document_id or trabajo["status"] not in ("queued", "running"):
                raise ValueError("El trabajo de ingestión no está disponible")
            self._conn.execute(
                "UPDATE document_admissions SET estado='encolada', actualizado_en=? WHERE document_id=?",
                (_iso(_ahora()), document_id),
            )
            if registro["clave_idempotencia"] is not None:
                self.idempotencia_completar(
                    registro["workspace_id"],
                    "upload",
                    registro["clave_idempotencia"],
                    document_id,
                    {"document_id": document_id, "job_id": registro["job_id"]},
                )
            self._conn.execute(
                "DELETE FROM document_cleanup WHERE document_id=? AND estado='reservada'", (document_id,)
            )

    def fallar_admision_documental(
        self, document_id: str, codigo: str, mensaje: str, *, respuesta_error: dict | None = None
    ) -> None:
        with self.transaccion():
            fila = self._conn.execute(
                "SELECT * FROM document_admissions WHERE document_id=?", (document_id,)
            ).fetchone()
            if fila is None or fila["estado"] == "fallida":
                return
            self.actualizar_documento(document_id, DocumentStatus.FAILED, detalle_error=mensaje)
            self._conn.execute(
                "UPDATE document_admissions SET estado='fallida', error_code=?, actualizado_en=? WHERE document_id=?",
                (codigo, _iso(_ahora()), document_id),
            )
            self._conn.execute(
                """INSERT INTO document_cleanup (document_id, workspace_id, estado, solicitado_en)
                   VALUES (?, ?, 'pendiente', ?) ON CONFLICT(document_id) DO UPDATE SET estado='pendiente'""",
                (document_id, fila["workspace_id"], _iso(_ahora())),
            )
            if fila["clave_idempotencia"] is not None and respuesta_error is not None:
                # Cuerpo público seguro, nunca la excepción cruda. Conserva la
                # reserva y su estado: un fallo no habilita repetir el efecto.
                _validar_respuesta_cacheada(respuesta_error)
                self._conn.execute(
                    "UPDATE idempotency_keys SET respuesta_json=? WHERE workspace_id=? AND operacion='upload' AND clave=?",
                    (
                        json.dumps({"error": respuesta_error}, ensure_ascii=False),
                        fila["workspace_id"],
                        fila["clave_idempotencia"],
                    ),
                )

    @_sincronizado
    def listar_limpiezas_documentales(self, *, limit: int = 100, cursor: str = "") -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit debe estar entre 1 y 100")
        return [
            dict(f)
            for f in self._conn.execute(
                """SELECT * FROM document_cleanup WHERE estado='pendiente' AND document_id>?
               AND (proximo_intento_en IS NULL OR proximo_intento_en<=?) ORDER BY document_id LIMIT ?""",
                (cursor, _iso(_ahora()), limit),
            ).fetchall()
        ]

    @_sincronizado
    def registrar_intento_limpieza_documental(
        self,
        workspace_id: str,
        document_id: str,
        *,
        error_code: str | None = None,
        proximo_intento: datetime | None = None,
    ) -> None:
        if proximo_intento is not None and proximo_intento.tzinfo is None:
            raise ValueError("El reintento debe tener zona horaria")
        self._conn.execute(
            """UPDATE document_cleanup SET intentos=intentos+1, ultimo_error=?, proximo_intento_en=?,
               estado=?, completado_en=? WHERE document_id=? AND workspace_id=? AND estado='pendiente'""",
            (
                error_code,
                _iso(proximo_intento) if proximo_intento else None,
                "pendiente" if error_code else "completada",
                None if error_code else _iso(_ahora()),
                document_id,
                workspace_id,
            ),
        )

    @_sincronizado
    def agendar_efecto_documental(self, workspace_id: str, document_id: str) -> None:
        """Reserva compensación terminal; no equivale a purgar el original.

        También se invoca dentro del commit terminal. El registro de limpieza
        existente distingue reservada (derivado/diagnóstico) de pendiente
        (admisión fallida a purgar). Una solicitud nueva invalida un ack viejo.
        """
        previo = self._conn.execute(
            "SELECT solicitado_en FROM document_cleanup WHERE document_id=?", (document_id,)
        ).fetchone()
        ahora = _ahora()
        if previo:
            ahora = max(ahora, datetime.fromisoformat(previo["solicitado_en"]) + timedelta(microseconds=1))
        self._conn.execute(
            """INSERT INTO document_cleanup (document_id, workspace_id, estado, solicitado_en)
               VALUES (?, ?, 'reservada', ?) ON CONFLICT(document_id) DO UPDATE SET
               estado=CASE WHEN document_cleanup.estado='pendiente' THEN 'pendiente' ELSE 'reservada' END,
               solicitado_en=excluded.solicitado_en, completado_en=NULL, proximo_intento_en=NULL""",
            (document_id, workspace_id, _iso(ahora)),
        )

    @_sincronizado
    def recuperar_efectos_documentales(self) -> None:
        """Materializa terminales antiguos cuyo observador no dejó seguimiento.

        La fuente durable es el terminal SQLite. No reabre efectos ya
        completados ni interpreta un manifiesto como éxito del pipeline.
        """
        self._conn.execute(
            """INSERT INTO document_cleanup (document_id, workspace_id, estado, solicitado_en)
               SELECT d.document_id, d.workspace_id, 'reservada', ? FROM documents d
               WHERE (EXISTS (SELECT 1 FROM jobs j WHERE j.document_id=d.document_id
                 AND j.workspace_id=d.workspace_id AND j.status IN ('failed','cancelled','rejected_quality'))
                 OR EXISTS (SELECT 1 FROM pending_deletes p WHERE p.workspace_id=d.workspace_id
                    AND ((p.recurso_tipo='document' AND p.recurso_id=d.document_id)
                         OR (p.recurso_tipo='workspace' AND p.recurso_id=d.workspace_id))))
               AND NOT EXISTS (SELECT 1 FROM document_cleanup c WHERE c.document_id=d.document_id)""",
            (_iso(_ahora()),),
        )

    @_sincronizado
    def listar_efectos_documentales(self, *, cursor: str = "", limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit debe estar entre 1 y 100")
        return [
            dict(fila)
            for fila in self._conn.execute(
                """SELECT c.*, a.estado AS admision_estado, COALESCE(a.job_id,d.recuperacion_job_id) AS job_id,
                          j.status AS job_status, d.hash, d.estado AS documento_estado, a.escritura_en_curso,
                          d.recuperacion_evidencia
                   FROM document_cleanup c JOIN documents d USING(document_id)
                   LEFT JOIN document_admissions a USING(document_id)
                   LEFT JOIN jobs j ON j.job_id=COALESCE(a.job_id,d.recuperacion_job_id)
                   WHERE c.estado IN ('reservada','pendiente') AND c.document_id>?
                     AND (c.proximo_intento_en IS NULL OR c.proximo_intento_en<=?)
                     AND (a.estado='fallida' OR j.status IN ('failed','cancelled','rejected_quality')
                          OR EXISTS (SELECT 1 FROM pending_deletes p WHERE p.workspace_id=c.workspace_id
                            AND ((p.recurso_tipo='document' AND p.recurso_id=c.document_id)
                                 OR (p.recurso_tipo='workspace' AND p.recurso_id=c.workspace_id))))
                   ORDER BY c.document_id LIMIT ?""",
                (cursor, _iso(_ahora()), limit),
            ).fetchall()
        ]

    @_sincronizado
    def registrar_resultado_efecto_documental(self, efecto: dict, *, error_code: str | None = None) -> bool:
        """Ack condicional: un efecto nuevo nunca se pierde por un intento anterior."""
        cursor = self._conn.execute(
            """UPDATE document_cleanup SET intentos=intentos+1, ultimo_error=?, proximo_intento_en=?,
               estado=CASE WHEN ? IS NULL THEN 'completada' ELSE estado END, completado_en=?
               WHERE document_id=? AND workspace_id=? AND solicitado_en=?
                 AND estado IN ('reservada','pendiente')""",
            (
                error_code,
                _iso(_ahora() + timedelta(seconds=60)) if error_code else None,
                error_code,
                None if error_code else _iso(_ahora()),
                efecto["document_id"],
                efecto["workspace_id"],
                efecto["solicitado_en"],
            ),
        )
        return cursor.rowcount == 1

    def retirar_documento(self, token: str, document_id: str) -> bool:
        """Lápida y obligación indivisibles; no llama a storage ni al gestor."""
        with self.transaccion():
            sesion = self.obtener_sesion(token)
            documento = self.obtener_documento(document_id)
            if sesion is None or documento is None or documento["workspace_id"] != sesion["workspace_id"]:
                return False
            self.agendar_borrado("document", document_id, sesion["workspace_id"])
            self.agendar_efecto_documental(sesion["workspace_id"], document_id)
            self._conn.execute("UPDATE document_cleanup SET estado='pendiente' WHERE document_id=?", (document_id,))
            self.purgar_datos_documentales(sesion["workspace_id"], document_id)
            return True

    @_sincronizado
    def finalizar_escritura_admision(self, document_id: str) -> None:
        self._conn.execute("UPDATE document_admissions SET escritura_en_curso=0 WHERE document_id=?", (document_id,))

    @_sincronizado
    def escritura_documental_en_curso(self, workspace_id: str, document_id: str) -> bool:
        return (
            self._conn.execute(
                """SELECT 1 FROM document_admissions
            WHERE workspace_id=? AND document_id=? AND escritura_en_curso=1""",
                (workspace_id, document_id),
            ).fetchone()
            is not None
        )

    @_sincronizado
    def recursos_documentales(self, workspace_id: str, document_id: str) -> tuple[list[str], list[dict]]:
        """Relaciones persistidas, incluso después de la retirada; nunca inferidas."""
        generaciones = [
            r[0]
            for r in self._conn.execute(
                """SELECT DISTINCT g.generation_id FROM generations g
                   LEFT JOIN generation_documents gd ON gd.generation_id = g.generation_id
                   WHERE g.workspace_id=? AND (g.document_id=? OR gd.document_id=?)""",
                (workspace_id, document_id, document_id),
            )
        ]
        trabajos = [
            dict(r)
            for r in self._conn.execute(
                """SELECT DISTINCT j.* FROM jobs j
                   LEFT JOIN job_documents jd ON jd.job_id = j.job_id
                   LEFT JOIN generations g ON g.generation_id = j.generation_id
                   LEFT JOIN generation_documents gd ON gd.generation_id = g.generation_id
                   WHERE j.workspace_id=? AND (
                       j.document_id=?
                       OR jd.document_id=?
                       OR (g.workspace_id=? AND (g.document_id=? OR gd.document_id=?))
                   )""",
                (workspace_id, document_id, document_id, workspace_id, document_id, document_id),
            )
        ]
        return generaciones, trabajos

    def purgar_datos_documentales(self, workspace_id: str, document_id: str) -> None:
        """El llamador mantiene transacción: conserva control, elimina payloads/referencias."""
        with self._lock:
            if not self._conn.in_transaction:
                raise ValueError("La purga operativa requiere una transacción")
            if not self.esta_borrado("document", document_id):
                raise ValueError("La purga requiere una lápida")
            generaciones, trabajos = self.recursos_documentales(workspace_id, document_id)
            for trabajo in trabajos:
                self._conn.execute(
                    "UPDATE jobs SET resultado=NULL, error_message=NULL WHERE job_id=?", (trabajo["job_id"],)
                )
                self._conn.execute(
                    "DELETE FROM events WHERE job_id=? AND workspace_id=?", (trabajo["job_id"], workspace_id)
                )
            for generacion in generaciones:
                self._conn.execute("DELETE FROM approved_outputs WHERE generation_id=?", (generacion,))
                self._conn.execute("UPDATE generations SET parametros_json=NULL WHERE generation_id=?", (generacion,))
                self._conn.execute("UPDATE generations SET error_message=NULL WHERE generation_id=?", (generacion,))
                self._conn.execute(
                    "DELETE FROM events WHERE generation_id=? AND workspace_id=?", (generacion, workspace_id)
                )
            progreso = self.obtener_progreso_restaurado(workspace_id)
            if progreso is not None:
                nuevo = retirar_referencias(progreso, document_id, set(generaciones)) or {
                    "version": progreso["version"]
                }
                if nuevo != progreso:
                    nuevo["version"] = progreso["version"] + 1
                    self._conn.execute(
                        "UPDATE workspace_progress SET version=?, state_json=?, actualizado_en=? WHERE workspace_id=?",
                        (
                            nuevo["version"],
                            json.dumps(nuevo, ensure_ascii=False, allow_nan=False),
                            _iso(_ahora()),
                            workspace_id,
                        ),
                    )
            self._conn.execute(
                """UPDATE documents SET titulo='Documento retirado', source_name=NULL,
                metadatos_json=NULL, cobertura_json=NULL, detalle_error=NULL WHERE document_id=? AND workspace_id=?""",
                (document_id, workspace_id),
            )

    @_sincronizado
    def limpieza_documental_completada(self, workspace_id: str, document_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM document_cleanup WHERE document_id=? AND workspace_id=? AND estado='completada'",
                (document_id, workspace_id),
            ).fetchone()
            is not None
        )

    # ------------------------------------------------------------------
    # Generaciones
    # ------------------------------------------------------------------

    def registrar_generacion(
        self,
        generation_id: str,
        workspace_id: str,
        document_id: str,
        status: JobStatus = JobStatus.QUEUED,
        document_ids: list[str] | None = None,
        snapshot: list[object] | None = None,
    ) -> None:
        ahora = _iso(_ahora())
        with self._lock:
            doc_ids = sorted(list(set(document_ids))) if document_ids else [document_id]
            if document_id not in doc_ids:
                doc_ids.insert(0, document_id)
            docs_map = {}
            for d_id in doc_ids:
                doc = self.obtener_documento(d_id)
                if doc is None or doc["workspace_id"] != workspace_id:
                    raise ValueError("El documento no pertenece al espacio o fue retirado")
                docs_map[d_id] = doc
            snapshot_json = None
            if snapshot:
                snapshot_json = json.dumps(
                    [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in snapshot],
                    ensure_ascii=False,
                )
            self._conn.execute(
                """
                INSERT INTO generations (generation_id, workspace_id, document_id, status, creado_en, actualizado_en, snapshot_fuentes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (generation_id, workspace_id, document_id, status.value, ahora, ahora, snapshot_json),
            )
            for d_id in doc_ids:
                d_info = docs_map[d_id]
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO generation_documents (generation_id, document_id, titulo, hash, version)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (generation_id, d_id, d_info.get("titulo"), d_info.get("hash"), "v1"),
                )

    @_sincronizado
    def registrar_generacion_retirada(
        self, generation_id: str, workspace_id: str, document_id: str, document_ids: list[str] | None = None
    ) -> None:
        """Relación interna de purga; exige la lápida y nunca autoriza el resultado."""
        doc_ids = document_ids or [document_id]
        for d_id in doc_ids:
            documento = self._conn.execute("SELECT workspace_id FROM documents WHERE document_id=?", (d_id,)).fetchone()
            if (
                documento is None
                or documento["workspace_id"] != workspace_id
                or not self.esta_borrado("document", d_id)
            ):
                raise ValueError("La relación de purga requiere un documento retirado del mismo espacio")
        ahora = _iso(_ahora())
        self._conn.execute(
            """INSERT INTO generations (generation_id, workspace_id, document_id, status, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (generation_id, workspace_id, document_id, JobStatus.COMPLETED.value, ahora, ahora),
        )
        for d_id in doc_ids:
            self._conn.execute(
                """INSERT OR IGNORE INTO generation_documents (generation_id, document_id, titulo, hash, version)
                VALUES (?, ?, ?, ?, ?)""",
                (generation_id, d_id, "Documento retirado", None, "v1"),
            )

    def actualizar_generacion(
        self, generation_id: str, status: JobStatus, error_code: str | None = None, error_message: str | None = None
    ) -> None:
        """Transición de estado del trabajo (queued→running→...→terminal)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE generations
                   SET status = ?, error_code = ?, error_message = ?, actualizado_en = ?
                 WHERE generation_id = ?
                """,
                (status.value, error_code, error_message, _iso(_ahora()), generation_id),
            )

    @_sincronizado
    def documentos_generacion(self, generation_id: str) -> list[str]:
        """IDs de todos los documentos vinculados a la generación."""
        filas = self._conn.execute(
            "SELECT document_id FROM generation_documents WHERE generation_id=? ORDER BY rowid",
            (generation_id,),
        ).fetchall()
        if filas:
            return [r[0] for r in filas]
        gen = self._conn.execute(
            "SELECT document_id FROM generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        return [gen[0]] if gen and gen[0] else []

    @_sincronizado
    def snapshot_generacion(self, generation_id: str) -> list[dict] | None:
        fila = self._conn.execute(
            "SELECT snapshot_fuentes_json FROM generations WHERE generation_id=?",
            (generation_id,),
        ).fetchone()
        if fila and fila[0]:
            return json.loads(fila[0])
        return None

    @_sincronizado
    def documentos_ready(self, workspace_id: str) -> list[dict]:
        filas = self._conn.execute(
            """SELECT d.* FROM documents d
               JOIN workspaces w ON w.workspace_id = d.workspace_id
               WHERE d.workspace_id = ? AND d.estado = 'ready'
                 AND w.borrado_en IS NULL AND w.expira_en > ?
                 AND NOT EXISTS (
                     SELECT 1 FROM pending_deletes r
                     WHERE (r.recurso_tipo = 'document' AND r.recurso_id = d.document_id)
                        OR (r.recurso_tipo = 'workspace' AND r.recurso_id = d.workspace_id)
                 )
               ORDER BY d.creado_en ASC, d.document_id ASC""",
            (workspace_id, _iso(_ahora())),
        ).fetchall()
        return [dict(f) for f in filas]

    @_sincronizado
    def obtener_generacion(self, generation_id: str) -> dict | None:
        """Trabajo solo si existe y nadie lo retiró (lápida propia o de cualquiera de sus fuentes)."""
        if self.esta_borrado("generation", generation_id):
            return None
        fila = self._conn.execute(
            """
            SELECT g.* FROM generations g
            JOIN workspaces w ON w.workspace_id = g.workspace_id
            WHERE g.generation_id = ? AND w.borrado_en IS NULL AND w.expira_en > ?
            """,
            (generation_id, _iso(_ahora())),
        ).fetchone()
        if not fila:
            return None
        if self.esta_borrado("document", fila["document_id"]):
            return None
        docs_gen = self.documentos_generacion(generation_id)
        if any(self.esta_borrado("document", d_id) for d_id in docs_gen):
            return None
        return dict(fila)

    @_sincronizado
    def listar_generaciones(self, workspace_id: str) -> list[dict]:
        """Listado del espacio (GET /api/generations) sin recursos borrados ni fuentes retiradas."""
        ahora = _iso(_ahora())
        filas = self._conn.execute(
            """
            SELECT g.* FROM generations g
            JOIN workspaces w ON w.workspace_id = g.workspace_id
            WHERE g.workspace_id = ?
              AND w.borrado_en IS NULL AND w.expira_en > ?
              AND NOT EXISTS (
                  SELECT 1 FROM pending_deletes pd
                   WHERE pd.recurso_tipo = 'generation' AND pd.recurso_id = g.generation_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM pending_deletes pd2
                   WHERE pd2.recurso_tipo = 'document'
                     AND (pd2.recurso_id = g.document_id
                          OR pd2.recurso_id IN (
                              SELECT gd.document_id FROM generation_documents gd
                              WHERE gd.generation_id = g.generation_id
                          ))
              )
            ORDER BY g.creado_en DESC
            """,
            (workspace_id, ahora),
        ).fetchall()
        return [dict(fila) for fila in filas]

    # ------------------------------------------------------------------
    # Trabajos comunes (issue #20): ingestion, chat y glosario
    # ------------------------------------------------------------------

    @_sincronizado
    def guardar_parametros_generacion(self, generation_id: str, parametros: dict, *, creado_en: str | None = None):
        fila = self.obtener_generacion(generation_id)
        if fila is None or parametros["document_id"] != fila["document_id"]:
            raise ValueError("Los parámetros no corresponden a la generación")
        self._conn.execute(
            "UPDATE generations SET parametros_json=?, creado_en=COALESCE(?, creado_en) WHERE generation_id=?",
            (json.dumps(parametros, sort_keys=True, ensure_ascii=False), creado_en, generation_id),
        )

    @_sincronizado
    def job_generacion(self, generation_id: str) -> dict | None:
        fila = self._conn.execute(
            "SELECT * FROM jobs WHERE generation_id=? ORDER BY rowid DESC LIMIT 1", (generation_id,)
        ).fetchone()
        return dict(fila) if fila else None

    @_sincronizado
    def historial_chat(
        self,
        workspace_id: str,
        document_id: str,
        document_hash: str,
        limite: int,
        scope_hash: str | None = None,
    ):
        if not 0 <= limite <= 50:
            raise ValueError("Límite de turnos inválido")
        target_scope = scope_hash or document_hash
        filas = self._conn.execute(
            """SELECT resultado FROM jobs WHERE workspace_id=?
               AND tipo='chat' AND status='completed' AND resultado IS NOT NULL
               AND (
                   scope_hash=?
                   OR (scope_hash IS NULL AND document_id=? AND json_extract(resultado, '$.document_hash')=?)
               )
               ORDER BY creado_en DESC, job_id DESC LIMIT ?""",
            (workspace_id, target_scope, document_id, document_hash, limite),
        ).fetchall()
        return [json.loads(fila[0]) for fila in reversed(filas)]

    @_sincronizado
    def guardar_aprobado(self, generation_id: str, paquete_json: str, expira_en: str):
        from app.schemas.responses import cargar_paquete_json, modo_paquete

        paquete = cargar_paquete_json(paquete_json)
        vencimiento = datetime.fromisoformat(expira_en)
        if vencimiento.tzinfo is None or not _ahora() < vencimiento <= _ahora() + timedelta(hours=24):
            raise ValueError("La retención debe vencer dentro de 24 horas")
        fila = self.obtener_generacion(generation_id)
        docs_gen = self.documentos_generacion(generation_id)
        if (
            fila is None
            or len(paquete_json.encode()) > 2_000_000
            or paquete.generation_id != generation_id
            or (
                paquete.documento_fuente.document_id != fila["document_id"]
                and paquete.documento_fuente.document_id not in docs_gen
            )
            or paquete.almacenamiento_oci.objeto_id != f"outputs/{fila['workspace_id']}/{generation_id}/content.json"
            or modo_paquete(paquete)
            != json.loads(fila["parametros_json"] or "{}").get("modo_generacion", "multiagente")
        ):
            raise ValueError("El aprobado no corresponde a su generación")
        if getattr(paquete, "documentos_fuente", None):
            fuentes_ids = {f.document_id for f in paquete.documentos_fuente}
            if docs_gen and not fuentes_ids.issubset(set(docs_gen)):
                raise ValueError("El aprobado contiene fuentes no asociadas a la generación")
        self._conn.execute(
            "INSERT OR IGNORE INTO approved_outputs VALUES (?, ?, ?)", (generation_id, paquete_json, expira_en)
        )
        if (
            self._conn.execute(
                "SELECT paquete_json FROM approved_outputs WHERE generation_id=?", (generation_id,)
            ).fetchone()[0]
            != paquete_json
        ):
            raise ValueError("El aprobado retenido no puede sustituirse")

    @_sincronizado
    def aprobado_retenido(self, generation_id: str) -> str | None:
        self.purgar_aprobados_expirados()
        if self.obtener_generacion(generation_id) is None:
            return None
        fila = self._conn.execute(
            "SELECT paquete_json FROM approved_outputs WHERE generation_id=? AND expira_en>?",
            (generation_id, _iso(_ahora())),
        ).fetchone()
        return fila[0] if fila else None

    @_sincronizado
    def descartar_aprobado(self, generation_id: str):
        self._conn.execute("DELETE FROM approved_outputs WHERE generation_id=?", (generation_id,))

    @_sincronizado
    def purgar_aprobados_expirados(self):
        self._conn.execute("DELETE FROM approved_outputs WHERE expira_en<=?", (_iso(_ahora()),))

    @_sincronizado
    def abandonar_reserva_generacion(self, workspace_id, generation_id, operacion, clave, *, eliminar=False):
        if clave is not None:
            self._conn.execute(
                """DELETE FROM idempotency_keys WHERE workspace_id=? AND operacion=? AND clave=? AND recurso_id=?
                   AND (? != 'generate' OR NOT EXISTS (SELECT 1 FROM jobs WHERE generation_id=?))""",
                (workspace_id, operacion, clave, generation_id, operacion, generation_id),
            )
        if eliminar:
            self._conn.execute(
                """DELETE FROM generations WHERE workspace_id=? AND generation_id=?
                   AND NOT EXISTS (SELECT 1 FROM jobs WHERE generation_id=generations.generation_id)""",
                (workspace_id, generation_id),
            )

    @_sincronizado
    def pagina_generaciones(self, workspace_id, *, despues=None, limit=20, **filtros):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Límite de página inválido")
        condiciones = [
            "g.workspace_id=?",
            "w.borrado_en IS NULL",
            "w.expira_en>?",
            "NOT EXISTS (SELECT 1 FROM pending_deletes p WHERE p.recurso_tipo='generation' AND p.recurso_id=g.generation_id)",
            """NOT EXISTS (
                SELECT 1 FROM pending_deletes p
                WHERE p.recurso_tipo='document'
                  AND (p.recurso_id=g.document_id
                       OR p.recurso_id IN (
                           SELECT gd.document_id FROM generation_documents gd
                           WHERE gd.generation_id=g.generation_id
                       ))
            )""",
        ]
        valores = [workspace_id, _iso(_ahora())]
        if despues is not None:
            condiciones.append("(g.creado_en, g.generation_id)<(?, ?)")
            valores.extend(despues)
        columnas = {
            "perfil": "json_extract(g.parametros_json, '$.perfil_destinatario')",
            "formato": "json_extract(g.parametros_json, '$.formato_salida')",
            "idioma": "json_extract(g.parametros_json, '$.idioma_salida')",
        }
        for nombre, valor in filtros.items():
            if valor is None:
                continue
            if nombre == "documento":
                condiciones.append(
                    "(g.document_id=? OR EXISTS (SELECT 1 FROM generation_documents gd WHERE gd.generation_id=g.generation_id AND gd.document_id=?))"
                )
                valores.extend([valor, valor])
            elif nombre in columnas:
                condiciones.append(columnas[nombre] + "=?")
                valores.append(valor)
            elif nombre in ("desde", "hasta"):
                condiciones.append("g.creado_en" + (">=?" if nombre == "desde" else "<=?"))
                valores.append(valor)
            else:
                raise ValueError("Filtro no permitido")
        filas = self._conn.execute(
            "SELECT g.* FROM generations g JOIN workspaces w ON w.workspace_id=g.workspace_id WHERE "
            + " AND ".join(condiciones)
            + " ORDER BY g.creado_en DESC, g.generation_id DESC LIMIT ?",
            [*valores, limit],
        ).fetchall()
        return [dict(fila) for fila in filas]

    def registrar_job(self, job_id: str, workspace_id: str, tipo: str, status: JobStatus = JobStatus.QUEUED) -> None:
        """Registra un trabajo común en la cola (el gestor de #20 lo consume)."""
        ahora = _iso(_ahora())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (job_id, workspace_id, tipo, status, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, workspace_id, tipo, status.value, ahora, ahora),
            )

    @_sincronizado
    def encolar_job(
        self,
        job_id: str,
        workspace_id: str,
        tipo: str,
        generation_id: str | None = None,
        document_id: str | None = None,
        cache_key: str | None = None,
        document_ids: list[str] | None = None,
        scope_hash: str | None = None,
    ) -> None:
        """Acepta trabajo y evento juntos; la generación vinculada comparte su ciclo de vida."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            if self.obtener_workspace(workspace_id) is None:
                raise ValueError("El espacio no está disponible")
            docs_gen: list[str] = []
            if generation_id:
                generacion = self.obtener_generacion(generation_id)
                if not generacion or generacion["workspace_id"] != workspace_id or generacion["status"] != "queued":
                    raise ValueError("La generación no está disponible para encolar")
                docs_gen = self.documentos_generacion(generation_id)

            todos_doc_ids: list[str] = []
            if document_id:
                todos_doc_ids.append(document_id)
            if document_ids:
                for d in document_ids:
                    if d not in todos_doc_ids:
                        todos_doc_ids.append(d)
            for d in docs_gen:
                if d not in todos_doc_ids:
                    todos_doc_ids.append(d)

            if document_id is not None:
                documento = self.obtener_documento(document_id)
                if not documento or documento["workspace_id"] != workspace_id:
                    raise ValueError("El documento no está disponible para el trabajo")
                if generation_id and generacion["document_id"] != document_id and document_id not in docs_gen:
                    raise ValueError("La generación corresponde a otro documento")
                if tipo == "ingestion" and (generation_id or documento["estado"] != DocumentStatus.PROCESSING.value):
                    raise ValueError("El documento no está en procesamiento")
                if tipo != "ingestion" and (
                    documento["estado"] != DocumentStatus.READY.value
                    or (not generation_id and tipo not in ("chat", "glosario"))
                ):
                    raise ValueError("El documento no está listo para el trabajo")
                if tipo == "ingestion" and documento["recuperacion_evidencia"] is not None:
                    if documento["recuperacion_estado"] != "reservada" or documento["recuperacion_job_id"] != job_id:
                        raise ValueError("La reconstrucción no corresponde al intento reservado")
                    self._conn.execute(
                        """UPDATE documents SET recuperacion_estado='en_curso',
                           recuperacion_intentos=recuperacion_intentos+1 WHERE document_id=?""",
                        (document_id,),
                    )

            for d_id in todos_doc_ids:
                if d_id == document_id:
                    continue
                d_obj = self.obtener_documento(d_id)
                if not d_obj or d_obj["workspace_id"] != workspace_id:
                    raise ValueError("El documento no está disponible para el trabajo")
                if tipo != "ingestion" and d_obj["estado"] != DocumentStatus.READY.value:
                    raise ValueError("El documento no está listo para el trabajo")

            self.registrar_job(job_id, workspace_id, tipo)
            self._conn.execute(
                "UPDATE jobs SET generation_id=?, document_id=?, cache_key=?, scope_hash=? WHERE job_id=?",
                (generation_id, document_id, cache_key, scope_hash, job_id),
            )
            for d_id in todos_doc_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO job_documents (job_id, document_id) VALUES (?, ?)",
                    (job_id, d_id),
                )
            if generation_id:
                self._conn.execute(
                    """INSERT OR IGNORE INTO job_documents (job_id, document_id)
                       SELECT ?, document_id FROM generation_documents WHERE generation_id=?""",
                    (job_id, generation_id),
                )
            self.registrar_evento(
                workspace_id, "encolado", JobStatus.QUEUED, job_id=job_id, generation_id=generation_id
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_sincronizado
    def iniciar_job(self, job_id: str) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            fila = self.obtener_job(job_id)
            if not fila or fila["status"] != "queued":
                raise ValueError("El trabajo no está disponible")
            self.actualizar_job(job_id, JobStatus.RUNNING)
            if fila["generation_id"]:
                self.actualizar_generacion(fila["generation_id"], JobStatus.RUNNING)
            self.registrar_evento(
                fila["workspace_id"],
                fila["tipo"],
                JobStatus.RUNNING,
                job_id=job_id,
                generation_id=fila["generation_id"],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def actualizar_job(
        self,
        job_id: str,
        status: JobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        resultado: str | None = None,
        incremento_intentos: int = 0,
    ) -> None:
        """Transición de estado de un trabajo común (queued→running→terminal).

        `resultado` es JSON serializado por el CALLER (el gestor); acá solo
        se persiste la cadena — la forma del resultado la define cada tipo
        de trabajo y se documenta en contratos-api.md.
        """
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                   SET status = ?, error_code = ?, error_message = ?, resultado = ?,
                       intentos = intentos + ?, actualizado_en = ?
                 WHERE job_id = ?
                """,
                (status.value, error_code, error_message, resultado, incremento_intentos, _iso(_ahora()), job_id),
            )

    @_sincronizado
    def obtener_job(self, job_id: str) -> dict | None:
        """Trabajo solo si existe y su espacio no fue retirado (ownership
        se resuelve desde la sesión en la capa API, §7.2)."""
        if self.obtener_workspace(self._workspace_de_job(job_id) or "") is None:
            return None
        fila = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if fila is None:
            return None
        return dict(fila)

    @_sincronizado
    def job_cache_glosario(self, workspace_id: str, cache_key: str) -> dict | None:
        """Sólo aprobados confirmados o un intento activo de la misma combinación."""
        fila = self._conn.execute(
            """SELECT job_id FROM jobs WHERE workspace_id=? AND cache_key=? AND tipo='glosario'
               AND (status IN ('queued','running') OR (status='completed' AND resultado IS NOT NULL))
               ORDER BY creado_en DESC LIMIT 1""",
            (workspace_id, cache_key),
        ).fetchone()
        if fila is None:
            return None
        job = self.obtener_job(fila[0])
        return job if job and self.obtener_documento(job["document_id"]) is not None else None

    @_sincronizado
    def vincular_cache_job(self, job_id: str, cache_key: str):
        self._conn.execute("UPDATE jobs SET cache_key=? WHERE job_id=? AND tipo='glosario'", (cache_key, job_id))

    @_sincronizado
    def abandonar_intencion_job(self, workspace_id, operacion, clave, job_id):
        """Un rechazo de admisión no deja una intención que apunte a un job ausente."""
        self._conn.execute(
            """DELETE FROM idempotency_keys WHERE workspace_id=? AND operacion=? AND clave=? AND recurso_id=?
               AND NOT EXISTS (SELECT 1 FROM jobs WHERE job_id=?)""",
            (workspace_id, operacion, clave, job_id, job_id),
        )

    @_sincronizado
    def _workspace_de_job(self, job_id: str) -> str | None:
        fila = self._conn.execute("SELECT workspace_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return fila["workspace_id"] if fila else None

    @_sincronizado
    def primer_job_encolado(self) -> dict | None:
        """El trabajo en cola más antiguo del sistema (FIFO global, §7.5).

        La cola es GLOBAL (la ranura intensiva es una sola para todos los
        espacios y tipos de trabajo), ordenada por momento de llegada.
        """
        fila = self._conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY creado_en ASC LIMIT 1",
            (JobStatus.QUEUED.value,),
        ).fetchone()
        return dict(fila) if fila else None

    @_sincronizado
    def contar_trabajos_en_cola(self) -> int:
        """Cuántos trabajos esperan la ranura (para el límite de §7.5)."""
        fila = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = ?", (JobStatus.QUEUED.value,)
        ).fetchone()
        return int(fila["n"])

    @_sincronizado
    def hay_trabajo_activo_global(self) -> bool:
        """True si la única ranura intensiva está ocupada (§7.5: 1 global)."""
        fila = self._conn.execute("SELECT 1 FROM jobs WHERE status = ? LIMIT 1", (JobStatus.RUNNING.value,)).fetchone()
        return fila is not None

    @_sincronizado
    def contar_activos_de_workspace(self, workspace_id: str) -> int:
        """Trabajos activos (queued o running) del espacio: §7.5 fija 1."""
        fila = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE workspace_id = ? AND status IN (?, ?)",
            (workspace_id, JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        ).fetchone()
        return int(fila["n"])

    @_sincronizado
    def listar_jobs_encolados(self) -> list[dict]:
        return [
            dict(fila)
            for fila in self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY creado_en, rowid", (JobStatus.QUEUED.value,)
            ).fetchall()
        ]

    @_sincronizado
    def finalizar_job(self, job_id: str, status: JobStatus, **datos) -> None:
        """Publica estado y evento juntos, sin reabrir trabajos terminales ni espacios borrados."""
        confirmado: ResultadoIngestionDocumental | None = datos.pop("documento_confirmado", None)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            fila = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if fila and fila["status"] in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
                if status == JobStatus.COMPLETED and (
                    self.obtener_workspace(fila["workspace_id"]) is None
                    or (fila["generation_id"] and self.obtener_generacion(fila["generation_id"]) is None)
                    or (fila["document_id"] and self.obtener_documento(fila["document_id"]) is None)
                ):
                    status, datos = JobStatus.CANCELLED, {"error_message": "El espacio fue retirado."}
                if status == JobStatus.COMPLETED and fila["document_id"]:
                    documento = self.obtener_documento(fila["document_id"])
                    if documento and documento["estado"] == DocumentStatus.FAILED.value:
                        status, datos = (
                            JobStatus.FAILED,
                            {
                                "error_code": "INVALID_STATE",
                                "error_message": documento["detalle_error"]
                                or "La ingestión dejó el documento fallido.",
                            },
                        )
                if status == JobStatus.COMPLETED and confirmado is not None:
                    confirmado = ResultadoIngestionDocumental.model_validate(confirmado.model_dump())
                    documento = self.obtener_documento(fila["document_id"])
                    if (
                        fila["tipo"] != "ingestion"
                        or fila["status"] != JobStatus.RUNNING.value
                        or (fila["workspace_id"], fila["document_id"])
                        != (confirmado.workspace_id, confirmado.document_id)
                        or documento is None
                        or documento["hash"] != confirmado.hash_sha256
                        or documento["metadatos_json"] != confirmado.metadatos.model_dump_json()
                        or documento["cobertura_json"] != confirmado.cobertura.model_dump_json()
                    ):
                        raise ValueError("La confirmación no corresponde a la ingestión persistida")
                    self.actualizar_documento(fila["document_id"], confirmado.estado)
                    self._conn.execute(
                        """UPDATE documents SET recuperacion_estado='confirmada'
                           WHERE document_id=? AND recuperacion_job_id=? AND recuperacion_estado='en_curso'""",
                        (fila["document_id"], job_id),
                    )
                self.actualizar_job(job_id, status, **datos)
                if fila["generation_id"]:
                    self.actualizar_generacion(
                        fila["generation_id"], status, datos.get("error_code"), datos.get("error_message")
                    )
                if (
                    fila["tipo"] == "ingestion"
                    and fila["document_id"]
                    and status
                    in (
                        JobStatus.FAILED,
                        JobStatus.CANCELLED,
                        JobStatus.REJECTED_QUALITY,
                    )
                ):
                    self.actualizar_documento(
                        fila["document_id"],
                        DocumentStatus.FAILED,
                        detalle_error=datos.get("error_message") or "La ingestión no se completó.",
                    )
                    self.agendar_efecto_documental(fila["workspace_id"], fila["document_id"])
                    self._conn.execute(
                        """UPDATE documents SET recuperacion_estado='fallida'
                           WHERE document_id=? AND recuperacion_job_id=? AND recuperacion_estado='en_curso'""",
                        (fila["document_id"], job_id),
                    )
                self.registrar_evento(
                    fila["workspace_id"], fila["tipo"], status, job_id=job_id, generation_id=fila["generation_id"]
                )
            self._conn.execute("COMMIT")
        except Exception:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def marcar_huerfanos_como_interrumpidos(self) -> int:
        """Trabajos queued que sobrevivieron a su proceso → failed/INTERRUPTED.

        Un trabajo en cola vive en la BASE, pero la FUNCIÓN que lo ejecuta
        solo existe en la memoria del proceso que lo recibió: tras un
        reinicio nadie puede ejecutarlo. Igual que los running (§7.2), se
        marcan fallidos con causa visible; el usuario reintenta y la
        Idempotency-Key (§7.3) evita duplicar el efecto.
        """
        with self._lock:
            pendientes = self._conn.execute("SELECT job_id FROM jobs WHERE status IN ('queued', 'running')").fetchall()
            self._conn.execute(
                """UPDATE generations SET status='failed', error_code='INTERRUPTED',
                   error_message='La admisión se interrumpió antes de encolar.', actualizado_en=?
                   WHERE status='queued' AND parametros_json IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM jobs WHERE generation_id=generations.generation_id)""",
                (_iso(_ahora()),),
            )
            for fila in pendientes:
                self.finalizar_job(
                    fila["job_id"],
                    JobStatus.FAILED,
                    error_code="INTERRUPTED",
                    error_message="El proceso que aceptó el trabajo se reinició.",
                )
            return len(pendientes)

    # ------------------------------------------------------------------
    # Idempotencia (§7.3): el corazón atómico del issue
    # ------------------------------------------------------------------

    @_sincronizado
    def idempotencia_consultar(self, workspace_id, operacion, clave, cuerpo):
        """Consulta una intención ya admitida sin crear reservas ni requerir IA."""
        fila = self._conn.execute(
            "SELECT hash_cuerpo,recurso_id FROM idempotency_keys WHERE workspace_id=? AND operacion=? AND clave=?",
            (workspace_id, operacion, clave),
        ).fetchone()
        if fila is None:
            return None
        return ("duplicada", fila["recurso_id"]) if fila["hash_cuerpo"] == _hash_de(cuerpo) else ("conflicto", None)

    @_sincronizado
    def idempotencia_iniciar(
        self, workspace_id: str, operacion: str, clave: str, cuerpo: str, recurso_id: str | None = None
    ) -> tuple[str, str | None]:
        """Registra (o detecta) una intención. Devuelve (resultado, recurso_id).

        Es la operación que materializa los criterios 2 del issue, y su
        atomicidad descansa en la PRIMARY KEY (workspace, operacion, clave):

        - Primera vez: exige recurso_id reservado -> INSERT OK -> ("creada", None)
        - Misma clave + mismo cuerpo -> falla el INSERT -> ("duplicada", mismo recurso_id)
          "Duplicado en curso devuelve el mismo identificador; no crea
          otra generación" (§7.3). El llamador NO repite el trabajo: pasa
          el recurso_id que ya existe.
        - Misma clave + cuerpo distinto              -> ("conflicto", None)
          La API lo traduce a 409 IDEMPOTENCY_CONFLICT.

        Si un INSERT pierde una carrera (dos hilos con la misma clave), el
        segundo recibe IntegrityError y relee la fila ya insertada: el
        test-and-set sigue siendo atómico porque lo garantiza la PK de
        SQLite, no nuestro lock.
        """
        hash_cuerpo = _hash_de(cuerpo)
        fila = self._conn.execute(
            "SELECT hash_cuerpo, recurso_id FROM idempotency_keys WHERE workspace_id = ? AND operacion = ? AND clave = ?",
            (workspace_id, operacion, clave),
        ).fetchone()
        if fila is not None:
            return ("duplicada", fila["recurso_id"]) if fila["hash_cuerpo"] == hash_cuerpo else ("conflicto", None)
        if not recurso_id:
            raise ValueError("Una intención nueva exige recurso_id reservado antes de iniciar el trabajo")
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO idempotency_keys (workspace_id, operacion, clave, hash_cuerpo, recurso_id, estado, creado_en)
                    VALUES (?, ?, ?, ?, ?, 'en_curso', ?)
                    """,
                    (workspace_id, operacion, clave, hash_cuerpo, recurso_id, _iso(_ahora())),
                )
            return ("creada", None)
        except sqlite3.IntegrityError:
            fila = self._conn.execute(
                "SELECT * FROM idempotency_keys WHERE workspace_id = ? AND operacion = ? AND clave = ?",
                (workspace_id, operacion, clave),
            ).fetchone()
            if fila["hash_cuerpo"] != hash_cuerpo:
                return ("conflicto", None)
            return ("duplicada", fila["recurso_id"])

    def idempotencia_completar(
        self, workspace_id: str, operacion: str, clave: str, recurso_id: str, respuesta: dict | None = None
    ) -> None:
        """Cierra una intención: fija el recurso definitivo y (opcionalmente)
        una respuesta cacheada para reintentos idénticos.

        REGLA DE PRIVACIDAD (issue y §7.4): `respuesta` JAMÁS debe contener
        códigos de recuperación ni tokens en claro. Lo verifica el test
        `test_la_base_no_guarda_secretos_en_claro` leyendo los BYTES crudos
        del archivo.
        """
        _validar_respuesta_cacheada(respuesta)
        with self._lock:
            fila = self._conn.execute(
                "SELECT recurso_id FROM idempotency_keys WHERE workspace_id = ? AND operacion = ? AND clave = ?",
                (workspace_id, operacion, clave),
            ).fetchone()
            if fila is None or fila["recurso_id"] != recurso_id:
                raise ValueError("La respuesta debe conservar el recurso reservado")
            self._conn.execute(
                """
                UPDATE idempotency_keys
                   SET recurso_id = ?, estado = 'completada', respuesta_json = ?
                 WHERE workspace_id = ? AND operacion = ? AND clave = ?
                """,
                (
                    recurso_id,
                    json.dumps(respuesta, ensure_ascii=False) if respuesta is not None else None,
                    workspace_id,
                    operacion,
                    clave,
                ),
            )

    # ------------------------------------------------------------------
    # Eventos de progreso (§3.3)
    # ------------------------------------------------------------------

    def registrar_evento(
        self,
        workspace_id: str,
        step: str,
        status: JobStatus,
        generation_id: str | None = None,
        job_id: str | None = None,
        iteration: int | None = None,
    ) -> int:
        """Inserta un evento y devuelve su id monotónico (para SSE)."""
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events (workspace_id, generation_id, job_id, step, status, iteration, creado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace_id, generation_id, job_id, step, status.value, iteration, _iso(_ahora())),
            )
            return int(cursor.lastrowid)

    @_sincronizado
    def eventos_desde(self, generation_id: str, ultimo_id: int = 0) -> list[dict]:
        """Eventos del stream posteriors a `ultimo_id` (header Last-Event-ID, §3.3).

        Al reconectar, el cliente manda el último id que vio y recibe solo
        los nuevos: la reconexión NO re-genera ni duplica progreso.
        """
        filas = self._conn.execute(
            """
            SELECT * FROM events
             WHERE generation_id = ? AND id > ?
             ORDER BY id ASC
            """,
            (generation_id, ultimo_id),
        ).fetchall()
        return [dict(fila) for fila in filas]

    @_sincronizado
    def eventos_desde_job(self, job_id: str, ultimo_id: int = 0) -> list[dict]:
        """Cursor SSE para trabajos comunes; la API valida ownership antes de consultar."""
        filas = self._conn.execute(
            "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id ASC", (job_id, ultimo_id)
        ).fetchall()
        return [dict(fila) for fila in filas]

    # ------------------------------------------------------------------
    # Tombstones de borrado (§8.5, §11.5)
    # ------------------------------------------------------------------

    @_sincronizado
    def comprobar_limite_recursos(self, workspace_id: str, tabla: str, limite: int | None = None) -> None:
        if tabla not in ("documents", "generations"):
            raise ValueError("Recurso inválido")
        if limite is not None and (type(limite) is not int or limite <= 0):
            raise ValueError("El límite debe ser un entero positivo.")
        limite_efectivo = (
            limite
            if limite is not None
            else (self.limite_documentos if tabla == "documents" else self.limite_generaciones)
        )
        tipo, identificador = ("document", "document_id") if tabla == "documents" else ("generation", "generation_id")
        usados = self._conn.execute(
            f"""SELECT COUNT(*) FROM {tabla} r WHERE workspace_id=?
            AND NOT EXISTS(SELECT 1 FROM pending_deletes p WHERE p.recurso_tipo=? AND p.recurso_id=r.{identificador})
            AND NOT EXISTS(SELECT 1 FROM pending_deletes p WHERE p.recurso_tipo='document' AND p.recurso_id=r.document_id)""",
            (workspace_id, tipo),
        ).fetchone()[0]
        if usados >= limite_efectivo:
            raise ErrorAplicacion(
                ErrorCode.RATE_LIMITED,
                "Alcanzaste el límite de recursos conservados. Borrá uno para continuar.",
                {"motivo": "workspace_resource_limit", "recurso": tabla, "limite": limite_efectivo, "usados": usados},
            )

    @_sincronizado
    def expirar_workspaces(self):
        for fila in self._conn.execute(
            "SELECT workspace_id FROM workspaces WHERE borrado_en IS NULL AND expira_en<=?", (_iso(_ahora()),)
        ).fetchall():
            self.borrar_workspace(fila[0])
        # Incluye lápidas reconstruidas desde OCI sin resucitar sus datos.
        self._conn.execute(
            "INSERT OR IGNORE INTO workspace_cleanup(workspace_id) SELECT workspace_id FROM workspaces WHERE borrado_en IS NOT NULL"
        )

    @_sincronizado
    def limpiezas_espacios(self, pendientes=True):
        return [
            dict(f)
            for f in self._conn.execute(
                "SELECT c.*,w.borrado_en FROM workspace_cleanup c JOIN workspaces w USING(workspace_id)"
                + (" WHERE c.estado!='completada'" if pendientes else "")
                + " ORDER BY c.workspace_id"
            ).fetchall()
        ]

    @_sincronizado
    def resultado_limpieza_espacio(self, ws, estado, error=None):
        self._conn.execute(
            "UPDATE workspace_cleanup SET estado=?, intentos=intentos+1, error_code=?, actualizado_en=? WHERE workspace_id=?",
            (estado, error, _iso(_ahora()), ws),
        )

    @_sincronizado
    def recursos_espacio(self, ws):
        documentos = [
            r[0] for r in self._conn.execute("SELECT document_id FROM documents WHERE workspace_id=?", (ws,)).fetchall()
        ]
        trabajos = [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM jobs WHERE workspace_id=? AND status IN ('queued','running')", (ws,)
            ).fetchall()
        ]
        escrituras = (
            self._conn.execute(
                "SELECT 1 FROM document_admissions WHERE workspace_id=? AND escritura_en_curso=1", (ws,)
            ).fetchone()
            is not None
        )
        return documentos, trabajos, escrituras

    @_sincronizado
    def uso_espacio(self, ws: str) -> dict[str, int]:
        documentos = sum(
            self.obtener_documento(r[0]) is not None
            for r in self._conn.execute("SELECT document_id FROM documents WHERE workspace_id=?", (ws,)).fetchall()
        )
        generaciones = sum(
            self.obtener_generacion(r[0]) is not None
            for r in self._conn.execute("SELECT generation_id FROM generations WHERE workspace_id=?", (ws,)).fetchall()
        )
        return {
            "documentos": documentos,
            "limite_documentos": self.limite_documentos,
            "generaciones": generaciones,
            "limite_generaciones": self.limite_generaciones,
        }

    def purgar_datos_espacio(self, ws):
        with self.transaccion():
            self._conn.execute(
                "DELETE FROM events WHERE job_id IN (SELECT job_id FROM jobs WHERE workspace_id=?) OR generation_id IN (SELECT generation_id FROM generations WHERE workspace_id=?)",
                (ws, ws),
            )
            for tabla in ("jobs", "idempotency_keys", "workspace_progress", "sessions"):
                self._conn.execute(f"DELETE FROM {tabla} WHERE workspace_id=?", (ws,))
            self._conn.execute(
                "DELETE FROM approved_outputs WHERE generation_id IN (SELECT generation_id FROM generations WHERE workspace_id=?)",
                (ws,),
            )
            for tabla in ("generations", "document_cleanup", "document_admissions", "documents"):
                self._conn.execute(f"DELETE FROM {tabla} WHERE workspace_id=?", (ws,))

    def agendar_borrado(self, recurso_tipo: str, recurso_id: str, workspace_id: str) -> None:
        """Deja una lápida persistente; purga_despues_en indica elegibilidad para limpieza.

        Las lápidas viven en esta base y se sincronizan al manifiesto OCI
        mediante session/manifests.py: evitan que un borrado se "deshaga" por
        accidente — p. ej. una reconstrucción desde manifiestos que todavía
        menciona el recurso (§11.5: "el manifiesto de borrado pendiente
        evita recuperar recursos que el usuario ya retiró").
        """
        ahora = _ahora()
        with self._lock:
            if recurso_tipo != "workspace":
                self._conn.execute(
                    "UPDATE workspaces SET manifest_pendiente = 1, version_manifiesto = version_manifiesto + 1 WHERE workspace_id = ?",
                    (workspace_id,),
                )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO pending_deletes (recurso_tipo, recurso_id, workspace_id, solicitado_en, purga_despues_en)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    recurso_tipo,
                    recurso_id,
                    workspace_id,
                    _iso(ahora),
                    _iso(ahora + timedelta(days=self.dias_retencion)),
                ),
            )

    @_sincronizado
    def workspace_para_manifest(self, workspace_id: str) -> dict | None:
        """Incluye espacios retirados: solo para sincronización/recuperación interna."""
        fila = self._conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        return dict(fila) if fila else None

    def incorporar_lapidas_remotas(self, datos: dict) -> None:
        """Merge conservador previo al CAS del manifiesto privado existente."""
        ws = datos["workspace_id"]
        with self.transaccion():
            local = self.workspace_para_manifest(ws)
            if local is None or local["codigo_hash"] != datos["codigo_hash"]:
                raise ValueError("Manifiesto ajeno al espacio")
            for lapida in datos["recursos_borrados"]:
                tipo, identidad = lapida["recurso_tipo"], lapida["recurso_id"]
                if tipo == "workspace":
                    if identidad != ws:
                        raise ValueError("Lápida de otro espacio")
                else:
                    tabla, columna = (
                        ("documents", "document_id") if tipo == "document" else ("generations", "generation_id")
                    )
                    propietario = self._conn.execute(
                        f"SELECT workspace_id FROM {tabla} WHERE {columna}=?", (identidad,)
                    ).fetchone()
                    if propietario is not None and propietario["workspace_id"] != ws:
                        raise ValueError("Lápida de un recurso ajeno")
                self.restaurar_lapida(
                    ws,
                    lapida["recurso_tipo"],
                    lapida["recurso_id"],
                    lapida["solicitado_en"],
                    lapida["purga_despues_en"],
                )
                anterior = self._conn.execute(
                    """SELECT solicitado_en, purga_despues_en FROM pending_deletes
                    WHERE workspace_id=? AND recurso_tipo=? AND recurso_id=?""",
                    (ws, lapida["recurso_tipo"], lapida["recurso_id"]),
                ).fetchone()
                if anterior is None:
                    raise ValueError("Una lápida no puede cambiar de propietario")
                fechas = [
                    _iso(max(datetime.fromisoformat(anterior[campo]), datetime.fromisoformat(lapida[campo])))
                    for campo in ("solicitado_en", "purga_despues_en")
                ]
                self._conn.execute(
                    """UPDATE pending_deletes SET solicitado_en=?, purga_despues_en=?
                    WHERE workspace_id=? AND recurso_tipo=? AND recurso_id=?""",
                    (*fechas, ws, lapida["recurso_tipo"], lapida["recurso_id"]),
                )
            # No degradar fechas/versiones remotas ni reactivar un espacio.
            expira = max(datetime.fromisoformat(local["expira_en"]), datetime.fromisoformat(datos["expira_en"]))
            actividades = [x for x in (local["ultima_actividad_en"], datos.get("ultima_actividad_en")) if x]
            actividad = max((datetime.fromisoformat(x) for x in actividades), default=None)
            borrado = local["borrado_en"] or datos.get("borrado_en")
            if datos["borrado"] and borrado is None:
                borrado = _iso(_ahora())
            self._conn.execute(
                """UPDATE workspaces SET version_manifiesto=MAX(version_manifiesto, ?)+1,
                manifest_pendiente=1, expira_en=?, ultima_actividad_en=?, borrado_en=? WHERE workspace_id=?""",
                (datos["version"], _iso(expira), _iso(actividad) if actividad else None, borrado, ws),
            )

    @_sincronizado
    def registrar_actividad(self, workspace_id: str, dias_validez: int, intervalo_segundos: int = 300) -> bool:
        workspace = self.obtener_workspace(workspace_id)
        if workspace is None:
            return False
        ahora = _ahora()
        ultima = (
            datetime.fromisoformat(workspace["ultima_actividad_en"])
            if workspace["ultima_actividad_en"]
            else datetime.fromisoformat(workspace["expira_en"]) - timedelta(days=dias_validez)
        )
        if (ahora - ultima).total_seconds() < intervalo_segundos:
            return False
        self._conn.execute(
            """UPDATE workspaces SET ultima_actividad_en = ?, expira_en = ?, manifest_pendiente = 1,
               version_manifiesto = version_manifiesto + 1 WHERE workspace_id = ? AND borrado_en IS NULL""",
            (_iso(ahora), _iso(ahora + timedelta(days=dias_validez)), workspace_id),
        )
        return True

    @_sincronizado
    def listar_manifiestos_pendientes(self) -> list[str]:
        return [
            fila[0]
            for fila in self._conn.execute(
                "SELECT workspace_id FROM workspaces WHERE manifest_pendiente = 1 ORDER BY workspace_id"
            ).fetchall()
        ]

    @_sincronizado
    def confirmar_manifiesto(self, workspace_id: str, version: int) -> None:
        cursor = self._conn.execute(
            "UPDATE workspaces SET manifest_pendiente = 0 WHERE workspace_id = ? AND version_manifiesto = ?",
            (workspace_id, version),
        )
        if cursor.rowcount:
            self._conn.execute(
                "UPDATE pending_deletes SET manifest_confirmado_en = ? WHERE workspace_id = ?",
                (_iso(_ahora()), workspace_id),
            )

    @_sincronizado
    def listar_borrados(self, workspace_id: str) -> list[dict]:
        return [
            dict(fila)
            for fila in self._conn.execute(
                "SELECT recurso_tipo, recurso_id, solicitado_en, purga_despues_en FROM pending_deletes WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        ]

    @_sincronizado
    def registro_vacio(self) -> bool:
        return self._conn.execute("SELECT 1 FROM workspaces LIMIT 1").fetchone() is None

    @_sincronizado
    def restaurar_workspace(self, datos: dict) -> bool:
        workspace_id = datos["workspace_id"]
        if self.workspace_para_manifest(workspace_id) is not None:
            return False
        if self._conn.execute("SELECT 1 FROM workspaces WHERE codigo_hash = ?", (datos["codigo_hash"],)).fetchone():
            raise ValueError("Hash de recuperación compartido por espacios diferentes")
        self._conn.execute(
            """INSERT INTO workspaces (workspace_id, codigo_hash, codigo_creado_en, expira_en,
               borrado_en, version_manifiesto, ultima_actividad_en) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                workspace_id,
                datos["codigo_hash"],
                _iso(_ahora()),
                datos["expira_en"],
                datos.get("borrado_en"),
                datos["version"],
                datos.get("ultima_actividad_en"),
            ),
        )
        return True

    @_sincronizado
    def recuperacion_pendiente(self) -> bool:
        return bool(self._conn.execute("SELECT pendiente FROM recovery_state WHERE id = 1").fetchone()[0])

    @_sincronizado
    def confirmar_recuperacion(self) -> None:
        self._conn.execute("UPDATE recovery_state SET pendiente = 0 WHERE id = 1")

    @_sincronizado
    def restaurar_lapida(
        self, workspace_id: str, tipo: str, recurso_id: str, solicitado_en: str, purga_despues_en: str
    ) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO pending_deletes
               (recurso_tipo, recurso_id, workspace_id, solicitado_en, purga_despues_en, manifest_confirmado_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tipo, recurso_id, workspace_id, solicitado_en, purga_despues_en, _iso(_ahora())),
        )

    @_sincronizado
    def guardar_progreso_restaurado(self, workspace_id: str, state: dict) -> None:
        _validar_respuesta_cacheada(state)
        version = state.get("version")
        if type(version) is not int or version < 1:
            raise ValueError("Progreso sin versión válida")
        self._conn.execute(
            "INSERT INTO workspace_progress VALUES (?, ?, ?, ?)",
            (workspace_id, version, json.dumps(state, ensure_ascii=False, allow_nan=False), _iso(_ahora())),
        )

    @_sincronizado
    def obtener_progreso_restaurado(self, workspace_id: str) -> dict | None:
        if self.obtener_workspace(workspace_id) is None:
            return None
        fila = self._conn.execute(
            "SELECT state_json FROM workspace_progress WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        return json.loads(fila[0]) if fila else None

    @_sincronizado
    def confirmar_progreso(self, workspace_id: str, state: dict) -> None:
        """Replica un estado confirmado en OCI; nunca retrocede una versión local."""
        _validar_respuesta_cacheada(state)
        version = state.get("version")
        if type(version) is not int or version < 1:
            raise ValueError("Progreso sin versión válida")
        if self.obtener_workspace(workspace_id) is None:
            return
        self._conn.execute(
            """INSERT INTO workspace_progress VALUES (?, ?, ?, ?)
               ON CONFLICT(workspace_id) DO UPDATE SET
               version=excluded.version, state_json=excluded.state_json, actualizado_en=excluded.actualizado_en
               WHERE excluded.version >= workspace_progress.version""",
            (workspace_id, version, json.dumps(state, ensure_ascii=False, allow_nan=False), _iso(_ahora())),
        )

    @_sincronizado
    def esta_borrado(self, recurso_tipo: str, recurso_id: str) -> bool:
        """True mientras exista la lápida; el plazo nunca restaura acceso."""
        fila = self._conn.execute(
            "SELECT 1 FROM pending_deletes WHERE recurso_tipo = ? AND recurso_id = ?",
            (recurso_tipo, recurso_id),
        ).fetchone()
        return fila is not None

    def cerrar(self) -> None:
        """Cierra la conexión con checkpoint del WAL: todo queda en el archivo principal."""
        with self._lock:
            # wal_checkpoint(TRUNCATE): vuelca el log lateral al archivo y lo
            # vacía — copiar/solo-ver el .db tras cerrar es suficiente.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
