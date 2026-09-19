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

Límite honesto de alcance: si se PIERDE el volumen completo, este archivo
desaparece con él. La reconstrucción desde los manifiestos de OCI (con
tombstones DURABLES allá) es la parte que cierra el issue #14/#9; este
store expone lo que esa reconstrucción necesitará (invalidación de
sesiones por espacio, consulta de lápidas) pero no habla con OCI.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas.enums import DocumentStatus, JobStatus

# Versión actual del esquema. Cada cambio de esquema agrega una entrada a
# MIGRACIONES y sube este número; NUNCA se edita una migración ya aplicada
# (las bases reales de los usuarios quedaron con la vieja).
VERSION_ESQUEMA = 1

# Cada migración: (versión, SQL). Se aplican en orden ascendente dentro de
# una transacción cada una. La v1 crea todas las tablas del issue #08.
MIGRACIONES: list[tuple[int, str]] = [
    (
        1,
        """
        -- Espacios anónimos (§7.4): el código de recuperación SOLO como hash;
        -- borrado_en NOT NULL actúa como lápida del espacio completo.
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
        -- usuario". Viven hasta purga_despues_en (plazo de retención) para
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
]


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


class RegistroOperativo:
    """Acceso al registro operativo. Una instancia = UNA conexión escritora.

    Para tests y para la fábrica del app: la ruta de la base y el plazo de
    retención se pasan por parámetro (el default de producción sale de la
    configuración: DATA_DIR + retención de 30 días del Apéndice A).

    Thread-safety: el lock serializa las ESCRITURAS dentro del proceso
    (FastAPI atiende pedidos en varios hilos). Las lecturas van por la
    misma conexión y son seguras bajo WAL. EN UN SOLO PROCESO: dos procesos
    sobre la misma base violan el modelo de §14.2 y no está soportado.
    """

    def __init__(self, ruta_db: str | Path, dias_retencion: int = 30) -> None:
        self.ruta_db = Path(ruta_db)
        self.ruta_db.parent.mkdir(parents=True, exist_ok=True)
        self.dias_retencion = dias_retencion
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
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def crear_workspace(self, workspace_id: str, codigo_recuperacion: str, dias_validez: int) -> None:
        """Registra un espacio nuevo; del código se guarda SOLO el hash (§7.4)."""
        ahora = _ahora()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO workspaces (workspace_id, codigo_hash, codigo_creado_en, expira_en)
                VALUES (?, ?, ?, ?)
                """,
                (workspace_id, _hash_de(codigo_recuperacion), _iso(ahora), _iso(ahora + timedelta(days=dias_validez))),
            )

    def obtener_workspace(self, workspace_id: str) -> dict | None:
        """Datos del espacio, o None si no existe o fue borrado (lápida)."""
        fila = self._conn.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        if fila is None or fila["borrado_en"] is not None:
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
            self._conn.execute(
                "UPDATE workspaces SET borrado_en = ? WHERE workspace_id = ? AND borrado_en IS NULL",
                (_iso(_ahora()), workspace_id),
            )
        self.revocar_sesiones_de_workspace(workspace_id)
        self.agendar_borrado("workspace", workspace_id, workspace_id)

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
            SELECT s.*, w.borrado_en AS workspace_borrado_en
              FROM sessions s
              JOIN workspaces w ON w.workspace_id = s.workspace_id
             WHERE s.token_hash = ?
            """,
            (_hash_de(token),),
        ).fetchone()
        if fila is None or fila["revocada_en"] is not None or fila["workspace_borrado_en"] is not None:
            return None
        if datetime.fromisoformat(fila["expira_en"]) < _ahora():
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
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO documents (document_id, workspace_id, titulo, hash, estado, creado_en)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, workspace_id, titulo, hash_documento, estado.value, _iso(_ahora())),
            )

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

    def obtener_documento(self, document_id: str) -> dict | None:
        """Documento solo si existe y NADIE lo retiró: ni su espacio (lápida
        de workspace) ni él mismo (lápida de documento, §11.5)."""
        if self.esta_borrado("document", document_id):
            return None
        fila = self._conn.execute(
            """
            SELECT d.* FROM documents d
            JOIN workspaces w ON w.workspace_id = d.workspace_id
            WHERE d.document_id = ? AND w.borrado_en IS NULL
            """,
            (document_id,),
        ).fetchone()
        return dict(fila) if fila else None

    # ------------------------------------------------------------------
    # Generaciones
    # ------------------------------------------------------------------

    def registrar_generacion(
        self, generation_id: str, workspace_id: str, document_id: str, status: JobStatus = JobStatus.QUEUED
    ) -> None:
        ahora = _iso(_ahora())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO generations (generation_id, workspace_id, document_id, status, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (generation_id, workspace_id, document_id, status.value, ahora, ahora),
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

    def obtener_generacion(self, generation_id: str) -> dict | None:
        """Trabajo solo si existe y nadie lo retiró (lápida propia o del espacio)."""
        if self.esta_borrado("generation", generation_id):
            return None
        fila = self._conn.execute(
            """
            SELECT g.* FROM generations g
            JOIN workspaces w ON w.workspace_id = g.workspace_id
            WHERE g.generation_id = ? AND w.borrado_en IS NULL
            """,
            (generation_id,),
        ).fetchone()
        return dict(fila) if fila else None

    def listar_generaciones(self, workspace_id: str) -> list[dict]:
        """Listado del espacio (GET /api/generations) sin recursos borrados:
        excluye generaciones con lápida propia y espacios retirados."""
        filas = self._conn.execute(
            """
            SELECT g.* FROM generations g
            JOIN workspaces w ON w.workspace_id = g.workspace_id
            WHERE g.workspace_id = ?
              AND w.borrado_en IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM pending_deletes pd
                   WHERE pd.recurso_tipo = 'generation' AND pd.recurso_id = g.generation_id
                     AND pd.purga_despues_en > ?
              )
            ORDER BY g.creado_en DESC
            """,
            (workspace_id, _iso(_ahora())),
        ).fetchall()
        return [dict(fila) for fila in filas]

    # ------------------------------------------------------------------
    # Idempotencia (§7.3): el corazón atómico del issue
    # ------------------------------------------------------------------

    def idempotencia_iniciar(
        self, workspace_id: str, operacion: str, clave: str, cuerpo: str, recurso_id: str | None = None
    ) -> tuple[str, str | None]:
        """Registra (o detecta) una intención. Devuelve (resultado, recurso_id).

        Es la operación que materializa los criterios 2 del issue, y su
        atomicidad descansa en la PRIMARY KEY (workspace, operacion, clave):

        - Primera vez            -> INSERT OK          -> ("creada", None)
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
        with self._lock:
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

    # ------------------------------------------------------------------
    # Tombstones de borrado (§8.5, §11.5)
    # ------------------------------------------------------------------

    def agendar_borrado(self, recurso_tipo: str, recurso_id: str, workspace_id: str) -> None:
        """Deja lápida del recurso por el plazo de retención.

        Las lápidas viven EN ESTA BASE (y en los manifiestos OCI cuando #14
        llegue): su trabajo es evitar que un borrado se "deshaga" por
        accidente — p. ej. una reconstrucción desde manifiestos que todavía
        menciona el recurso (§11.5: "el manifiesto de borrado pendiente
        evita recuperar recursos que el usuario ya retiró").
        """
        ahora = _ahora()
        with self._lock:
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

    def esta_borrado(self, recurso_tipo: str, recurso_id: str) -> bool:
        """True si hay lápida vigente del recurso (aún no purgada)."""
        fila = self._conn.execute(
            "SELECT 1 FROM pending_deletes WHERE recurso_tipo = ? AND recurso_id = ? AND purga_despues_en > ?",
            (recurso_tipo, recurso_id, _iso(_ahora())),
        ).fetchone()
        return fila is not None

    def cerrar(self) -> None:
        """Cierra la conexión con checkpoint del WAL: todo queda en el archivo principal."""
        with self._lock:
            # wal_checkpoint(TRUNCATE): vuelca el log lateral al archivo y lo
            # vacía — copiar/solo-ver el .db tras cerrar es suficiente.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
