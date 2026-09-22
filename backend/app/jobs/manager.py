"""Gestor de trabajos: cola, estados, deadline y cancelación (issue #20, §7.5 y §14.2).

Qué es este archivo: el EJECUTOR de todo trabajo intensivo del sistema
(ingestión de documentos, generación pedagógica, chat y glosario). La API
solo REGISTRA intenciones; este gestor las corre una por una, con las
cuotas operativas acordadas en §7.5, y persiste cada transición de estado
en el registro de #8 (que a su vez alimenta el SSE de #31).

Por qué un worker DEDICADO y no el event loop (§14.2): "los trabajos
intensivos se ejecutan fuera del event loop para mantener API y SSE
disponibles". El gestor vive en un hilo propio: mientras un documento se
parsea o un paquete se genera, la API sigue respondiendo /health y las
consultas de estado. `enqueue` solo escribe en la base y devuelve — nunca
ejecuta nada en el hilo de quien llama.

El modelo de cuotas de §7.5, en lenguaje llano:

- RANURA GLOBAL (1): en todo el sistema corre UN trabajo intensivo por vez,
  compartido por todos los tipos y espacios ("Trabajo intensivo activo:
  Uno global"). Es la forma más simple de acotar el consumo del proveedor
  en una sola VM (§14.2: no escalamos horizontal sobre un índice local).
- COLA (≤5): hasta 5 trabajos esperando. El 6º pedido con la ranura ocupada
  se rechaza con 429 QUEUE_FULL y mensaje con la situación real.
- 1 POR ESPACIO: un espacio no puede tener dos trabajos activos/encolados
  a la vez (evita que un usuario llene la cola él solo).
- DEADLINE (300 s de EJECUCIÓN, sin contar la espera en cola): el trabajo
  recibe su fecha límite al ARRANCAR; si la pasa, termina failed/DEADLINE.
- ESPERA MÁXIMA en cola (300 s): un trabajo que nadie levantó en ese plazo
  expira con diagnóstico, sin ejecutarse.
- REINTENTOS (2) por llamada TRANSITORIA en ctx.llamar, con backoff exponencial + jitter
  (espera creciente con un componente aleatorio para no sincronizar reintentos).

Cancelación COOPERATIVA: el gestor no puede matar el hilo a mitad de
trabajo (en Python no hay forma segura); en su lugar, el trabajo recibe un
`ContextoEjecucion` con un flag de cancelación y su deadline, y la función
lo consulta ENTRE pasos (el grafo de #29 hará exactamente eso: "chequeos
de flag entre pasos"). Cancelar en cola sí es inmediato: se marca y el
worker la salta. En ejecución: el flag termina el grafo SIN publicar
contenido (criterio del issue).

Reinicio: las funciones ejecutables no sobreviven al proceso. Al arrancar,
`marcar_huerfanos_como_interrumpidos` del store deja los queued huérfanos
como failed/INTERRUPTED (los running ya los cubre #8); el usuario reintenta
y la Idempotency-Key evita duplicar el efecto (§7.3).

Cuotas del PROVEEDOR por modelo (RPM/TPM/RPD): la clase `CuotasProveedor`
cuenta llamadas y tokens por modelo — CONTANDO REINTENTOS — y bloquea
cuando se agota alguna ventana. "La cuota diaria agotada detiene nuevas
llamadas": el trabajo termina failed/CUOTA_AGOTADA, sin insistir cada
pocos segundos. Los valores por defecto son conservadores y se calibran
con la cuenta real (§7.5); se inyectan por configuración.

Los endpoints HTTP (GET /api/jobs/{id}, /events, /cancel) se cablean en
#31/#19 junto con las sesiones de #9: sin autenticación y ownership no se
exponen rutas (contratos-api.md los exige autenticados).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.jobs.store import RegistroOperativo
from app.schemas.enums import JobStatus

_GESTORES_ACTIVOS: dict[str, object] = {}
_GUARDIA_GESTORES = threading.Lock()


class ColaLlenaError(RuntimeError):
    """La cola alcanzó MAX_QUEUED_JOBS: la API lo traduce a 429 QUEUE_FULL.

    El mensaje explica la situación real (cuántos esperan) para que la UI
    pueda decir cuándo volver a intentar (§6.5: "explica si el límite es de
    espera, cuota o dependencia").
    """

    def __init__(self, en_cola: int, maximo: int) -> None:
        super().__init__(
            f"La cola de trabajos esta llena ({en_cola} en espera de un maximo de {maximo}). "
            "Reintentar en unos minutos."
        )
        self.en_cola = en_cola
        self.maximo = maximo


class EspacioOcupadoError(RuntimeError):
    """El espacio ya tiene un trabajo activo o en cola (§7.5: 1 por espacio)."""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(
            f"El espacio {workspace_id} ya tiene un trabajo en curso o en cola; "
            "esperar a que termine o cancelarlo antes de pedir otro."
        )
        self.workspace_id = workspace_id


class TrabajoCanceladoError(Exception):
    """Señal que levanta la FUNCIÓN del trabajo al ver el flag de cancelación.

    El gestor la traduce a estado cancelled: termina el grafo SIN publicar
    contenido (criterio del issue).
    """


class RechazoCalidadError(Exception):
    """El Critic agotó las revisiones sin aprobar; no es un fallo técnico."""


class ReintentableError(Exception):
    """Error TRANSITORIO (red, 5xx del proveedor): reintenta con backoff.

    El proveedor (#13) envuelve así sus fallos de red dentro de ctx.llamar.
    Fuera de esa operación el pipeline falla: no se repiten pasos exitosos.
    Los errores PERMANENTES (validación, lógica) deben usar excepciones
    normales: reintentarlos solo quema presupuesto.
    """

    def __init__(self, mensaje: str, retry_after: float | None = None) -> None:
        super().__init__(mensaje)
        self.retry_after = retry_after


class CuotaAgotadaError(RuntimeError):
    """Se agotó una ventana de cuota del proveedor (RPM/TPM/RPD, §7.5)."""


class DeadlineExcedidoError(Exception):
    """El trabajo pasó su fecha límite de ejecución (300 s de §7.5)."""


# --------------------------------------------------------------------------
# Cuotas por modelo del proveedor
# --------------------------------------------------------------------------


@dataclass
class CuotasModelo:
    """Límites por modelo. Defaults conservadores: se calibran con la cuenta
    real del equipo (§7.5: "no se fijan cifras universales")."""

    rpm: int = 10
    tpm: int = 100_000
    rpd: int = 200


class CuotasProveedor:
    """Contador central de llamadas por modelo: RPM, TPM y RPD.

    Por qué existe: §7.5 ordena "centralizar RPM/TPM/RPD por modelo para
    embeddings, visión, redacción y juez, contando reintentos". Si cada
    módulo contara por su cuenta, el límite real sería la suma de las
    copias. Un solo objeto con lock ve TODAS las llamadas del proceso.

    Ventanas: RPM/TPM son ventanas DESLIZANTES de 60 s (deque de marcas de
    tiempo/tokens); RPD es un contador por fecha (se reinicia solo al
    cambiar el día). Thread-safe: el worker y los endpoints consultan.
    """

    def __init__(self, cuotas_por_modelo: dict[str, CuotasModelo] | None = None) -> None:
        self._cuotas = cuotas_por_modelo or {}
        self._lock = threading.Lock()
        self._llamadas: dict[str, deque[float]] = {}
        self._tokens: dict[str, deque[tuple[float, int]]] = {}
        self._llamadas_dia: dict[str, tuple[str, int]] = {}

    def permitir(self, modelo: str, tokens_estimados: int = 0) -> None:
        """Registra UNA llamada y lanza CuotaAgotadaError si rompe alguna ventana.

        Registrar y decidir son la MISMA operación atómica (con el lock):
        dos hilos no pueden colarse dentro de la misma ventana.
        """
        ahora = time.monotonic()
        if tokens_estimados < 0:
            raise ValueError("tokens_estimados no puede ser negativo")
        hoy = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            cuotas = self._cuotas.get(modelo)
            if cuotas is None:
                # Sin cuotas explícitas no se permite consumir el proveedor.
                raise CuotaAgotadaError(f"Falta configurar cuotas para el modelo {modelo}")

            ventana_llamadas = self._llamadas.setdefault(modelo, deque())
            while ventana_llamadas and ahora - ventana_llamadas[0] > 60:
                ventana_llamadas.popleft()
            if len(ventana_llamadas) >= cuotas.rpm:
                raise CuotaAgotadaError(f"Cuota RPM agotada para {modelo} ({cuotas.rpm}/min)")

            ventana_tokens = self._tokens.setdefault(modelo, deque())
            while ventana_tokens and ahora - ventana_tokens[0][0] > 60:
                ventana_tokens.popleft()
            total_tokens = sum(t for _, t in ventana_tokens) + tokens_estimados
            if total_tokens > cuotas.tpm:
                raise CuotaAgotadaError(f"Cuota TPM agotada para {modelo} ({cuotas.tpm} tokens/min)")

            fecha, conteo = self._llamadas_dia.get(modelo, (hoy, 0))
            conteo = conteo + 1 if fecha == hoy else 1
            if conteo > cuotas.rpd:
                raise CuotaAgotadaError(f"Cuota diaria (RPD) agotada para {modelo} ({cuotas.rpd}/dia)")
            ventana_llamadas.append(ahora)
            ventana_tokens.append((ahora, tokens_estimados))
            self._llamadas_dia[modelo] = (hoy, conteo)

    def disponibles_hoy(self, modelo: str) -> int | None:
        """Cuántas llamadas quedan hoy para el modelo (None si no está
        configurado). Útil para el panel de límites de la UI (§6.5)."""
        hoy = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            cuotas = self._cuotas.get(modelo)
            if cuotas is None:
                return None
            fecha, conteo = self._llamadas_dia.get(modelo, (hoy, 0))
            return max(cuotas.rpd - (conteo if fecha == hoy else 0), 0)


# --------------------------------------------------------------------------
# Contexto de ejecución y gestor
# --------------------------------------------------------------------------


@dataclass
class ContextoEjecucion:
    """Lo que el gestor le entrega a la función del trabajo.

    La función consulta `cancelado()` y `deadline` ENTRE pasos (cancelación
    cooperativa): si el flag está o el plazo pasó, debe abortar rápido
    levantando TrabajoCanceladoError/DeadlineExcedidoError o simplemente
    terminar antes de publicar nada.
    """

    job_id: str
    workspace_id: str
    tipo: str
    deadline: float  # time.monotonic() límite de EJECUCIÓN
    timeout_por_llamada: float = 60.0
    cuotas: CuotasProveedor | None = field(default=None, repr=False)
    espacio_vigente: Callable[[], bool] = field(default=lambda: True, repr=False)
    _evento_cancelacion: threading.Event = field(repr=False, default_factory=threading.Event)

    reintentos_transitorios: int = 2
    base_backoff_segundos: float = 0.05
    registrar_reintento: Callable[[], None] = field(default=lambda: None, repr=False)

    def cancelado(self) -> bool:
        return self._evento_cancelacion.is_set()

    def chequear(self) -> None:
        """Helper para usar entre pasos: levanta la excepción que corresponde."""
        if self.cancelado() or not self.espacio_vigente():
            raise TrabajoCanceladoError()
        if time.monotonic() > self.deadline:
            raise DeadlineExcedidoError()

    def llamar(self, funcion: Callable[..., Any], *, modelo: str, tokens_estimados: int = 0) -> Any:
        """Reserva cuota; el adaptador debe aplicar timeout al SDK/HTTP.

        La llamada conserva la ranura hasta retornar aunque se cancele.
        Cada intento del proveedor debe pasar por este método.
        """
        for intento in range(self.reintentos_transitorios + 1):
            self.chequear()
            if self.cuotas is None:
                raise CuotaAgotadaError("No hay cuotas configuradas")
            self.cuotas.permitir(modelo, tokens_estimados)
            timeout = min(self.timeout_por_llamada, self.deadline - time.monotonic())
            inicio = time.monotonic()
            try:
                resultado = funcion(timeout=timeout)
                self.chequear()
                if time.monotonic() - inicio > timeout:
                    raise ReintentableError("La llamada excedió su timeout")
                return resultado
            except ReintentableError as error:
                self.chequear()
                if intento == self.reintentos_transitorios:
                    raise
                pausa = max(
                    self.base_backoff_segundos * 2**intento * random.uniform(0.75, 1.25), error.retry_after or 0
                )
                self._evento_cancelacion.wait(min(pausa, max(0, self.deadline - time.monotonic())))
                self.chequear()
                self.registrar_reintento()


@dataclass(frozen=True)
class ControlesOperativos:
    """Los controles de §7.5, configurables para tests con valores chicos."""

    max_en_cola: int = 5  # "Cola de espera: hasta cinco trabajos"
    deadline_ejecucion_segundos: float = 300.0  # "Deadline de generación: 300 s"
    espera_maxima_en_cola_segundos: float = 300.0  # "Espera en cola: hasta 300 s"
    timeout_por_llamada_segundos: float = 60.0  # "Timeout de llamada: 60 s"
    reintentos_transitorios: int = 2  # "Reintentos transitorios: hasta dos"
    base_backoff_segundos: float = 0.05  # base de backoff (tests la bajan)


class GestorTrabajos:
    """Cola FIFO global con un worker dedicado, sobre el registro de #8.

    Ciclo de vida de un trabajo:

        enqueue → queued → (worker lo toma) running → completed / failed /
        rejected_quality / cancelled; o queued → cancelled (en cola) o
        queued → failed/COLA_EXPIRADA (esperó demasiado).

    Cada transición se persiste (store.actualizar_job) Y se registra como
    evento (store.registrar_evento): los eventos son la base del SSE de
    progreso de #31, con id monotónico para Last-Event-ID.
    """

    def __init__(
        self,
        store: RegistroOperativo,
        controles: ControlesOperativos | None = None,
        cuotas: CuotasProveedor | None = None,
    ) -> None:
        self.store = store
        self.controles = controles or ControlesOperativos()
        self.cuotas = cuotas or CuotasProveedor()
        # Los EJECUTABLES viven solo en este proceso (dict job_id→función):
        # lo persistente (estados, eventos, resultados) vive en el store.
        self._ejecutables: dict[str, Callable[[ContextoEjecucion], Any]] = {}
        self._cancelaciones: dict[str, threading.Event] = {}
        # RLock (reentrante) como en el store: métodos públicos (p. ej.
        # cancelar) llaman a helpers que también lo toman; con un Lock
        # simple eso sería un self-deadlock (lo descubrió el test de
        # cancelación en cola).
        self._lock = threading.RLock()
        self._despertar = threading.Event()
        self._activo = True
        # Reinicio (§7.2/§14.2): nadie ejecutará los queued que sobrevivieron
        # a otro proceso. Marcarlos y que la idempotencia del caller resuelva.
        self._registro_id = str(store.ruta_db.resolve())
        with _GUARDIA_GESTORES:
            if self._registro_id in _GESTORES_ACTIVOS:
                raise RuntimeError("Ya existe un gestor para este registro")
            _GESTORES_ACTIVOS[self._registro_id] = self
        try:
            self.store.marcar_huerfanos_como_interrumpidos()
            self._hilo = threading.Thread(target=self._ciclo_worker, name="gestor-trabajos", daemon=True)
            self._hilo.start()
        except Exception:
            with _GUARDIA_GESTORES:
                _GESTORES_ACTIVOS.pop(self._registro_id, None)
            raise

    # ------------------------------------------------------------------
    # API del gestor (la usan los endpoints de #19/#31)
    # ------------------------------------------------------------------

    def enqueue(
        self,
        workspace_id: str,
        tipo: str,
        funcion: Callable[[ContextoEjecucion], Any],
        job_id: str | None = None,
        generation_id: str | None = None,
    ) -> str:
        """Registra un trabajo y devuelve su job_id. Rechaza según §7.5.

        El orden de los chequeos importa para el MENSAJE que verá el
        usuario: primero si SU espacio ya tiene algo (puede cancelarlo él
        mismo), después si la cola global está llena (tiene que esperar).
        """
        with self._lock:
            if not self._activo:
                raise RuntimeError("El gestor está detenido")
            if self.store.obtener_workspace(workspace_id) is None:
                raise ValueError("El espacio no está disponible")
            activos = self.store.contar_activos_de_workspace(workspace_id)
            if activos >= 1:
                raise EspacioOcupadoError(workspace_id)
            en_cola = self.store.contar_trabajos_en_cola()
            # La ranura global (1) + la cola (≤ max_en_cola) son el cupo
            # total del sistema: con la ranura ocupada, el cupo es la cola.
            if en_cola >= self.controles.max_en_cola:
                raise ColaLlenaError(en_cola, self.controles.max_en_cola)

            identificador = job_id or f"job_{uuid.uuid4().hex[:16]}"
            self.store.encolar_job(identificador, workspace_id, tipo, generation_id=generation_id)
            self._ejecutables[identificador] = funcion
            self._cancelaciones[identificador] = threading.Event()
        self._despertar.set()
        return identificador

    def estado(self, job_id: str) -> dict | None:
        """Consulta del trabajo (GET /api/jobs/{id} de #31): estado, error y
        posición si sigue en cola."""
        with self._lock:
            self._barrer_cola_expirada()
            trabajo = self.store.obtener_job(job_id)
        if trabajo is None:
            return None
        if trabajo["status"] == JobStatus.QUEUED.value:
            cola = self.store.listar_jobs_encolados()
            trabajo["posicion_cola"] = next((i for i, j in enumerate(cola, 1) if j["job_id"] == job_id), None)
        return trabajo

    def cancelar(self, job_id: str) -> bool:
        """Cancelación cooperativa (POST /api/jobs/{id}/cancel).

        - En COLA: marca cancelled acá mismo; el worker la salta.
        - En EJECUCIÓN: prende el flag; la función lo ve entre pasos y
          aborta SIN publicar contenido.
        Devuelve True si la cancelación aplica (no es terminal ya).
        """
        with self._lock:
            trabajo = self.store.obtener_job(job_id)
            if trabajo is None or trabajo["status"] not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
                return False
            if trabajo["status"] == JobStatus.QUEUED.value:
                self._finalizar(job_id, JobStatus.CANCELLED, "Cancelado por el usuario mientras esperaba en la cola.")
            else:
                evento = self._cancelaciones.get(job_id)
                if evento:
                    evento.set()
        return True

    def detener(self) -> None:
        """Apaga el worker (tests y cierre del proceso). Los trabajos running
        al morir el proceso quedan failed/INTERRUPTED vía #8."""
        with self._lock:
            self._activo = False
            for evento in self._cancelaciones.values():
                evento.set()
        self._despertar.set()
        self._hilo.join(timeout=5)
        if self._hilo.is_alive():
            raise RuntimeError("Hay una llamada en vuelo; no cerrar el store hasta que termine")
        with _GUARDIA_GESTORES:
            if _GESTORES_ACTIVOS.get(self._registro_id) is self:
                del _GESTORES_ACTIVOS[self._registro_id]

    # ------------------------------------------------------------------
    # Worker interno
    # ------------------------------------------------------------------

    def _ciclo_worker(self) -> None:
        """Hilo dedicado: toma el siguiente encolado, lo ejecuta, repite.

        Espera activa mínima: duerme hasta que haya algo nuevo (`_despertar`)
        o hasta 1 s (para barrer expiraciones de cola).
        """
        try:
            while self._activo:
                self._despertar.wait(timeout=1.0)
                self._despertar.clear()
                if not self._activo:
                    break
                with self._lock:
                    self._barrer_cola_expirada()
                    siguiente = self.store.primer_job_encolado()
                if siguiente is None:
                    continue
                self._ejecutar(siguiente)
                self._despertar.set()  # Consumir inmediatamente los trabajos que ya esperan.
        except Exception as error:
            with self._lock:
                self._activo = False
            logging.getLogger(__name__).error("Worker detenido por fallo de infraestructura: %s", type(error).__name__)
            try:
                self.store.marcar_huerfanos_como_interrumpidos()
            except Exception:
                logging.getLogger(__name__).error("Recuperación pendiente hasta restablecer el registro")

    def _barrer_cola_expirada(self) -> None:
        """Trabajos que esperaron más de la cuenta: failed/COLA_EXPIRADA."""
        for trabajo in self._trabajos_encolados():
            creado = datetime.fromisoformat(trabajo["creado_en"])
            esperado = (datetime.now(timezone.utc) - creado).total_seconds()
            if esperado > self.controles.espera_maxima_en_cola_segundos:
                self._finalizar(
                    trabajo["job_id"],
                    JobStatus.FAILED,
                    "El trabajo esperó en cola más del máximo; reintentar.",
                    error_code="COLA_EXPIRADA",
                )

    def _trabajos_encolados(self) -> list[dict]:
        return self.store.listar_jobs_encolados()

    def _ejecutar(self, trabajo: dict) -> None:
        """Corre UN trabajo con deadline, reintentos y cancelación."""
        job_id = trabajo["job_id"]
        workspace_id = trabajo["workspace_id"]

        with self._lock:
            actual = self.store.obtener_job(job_id)
            if actual is None:
                self._finalizar(job_id, JobStatus.CANCELLED, "El espacio fue retirado.")
                return
            if actual["status"] != JobStatus.QUEUED.value:
                return
            funcion = self._ejecutables.get(job_id)
            evento_cancelacion = self._cancelaciones.get(job_id, threading.Event())
            self.store.iniciar_job(job_id)
        if funcion is None:
            # Ejecutable perdido (no debería pasar: el mismo proceso lo encoló).
            self._finalizar(job_id, JobStatus.FAILED, "El trabajo perdió su ejecutable.", error_code="ORFANO")
            return

        contexto = ContextoEjecucion(
            job_id=job_id,
            workspace_id=workspace_id,
            tipo=trabajo["tipo"],
            deadline=time.monotonic() + self.controles.deadline_ejecucion_segundos,
            timeout_por_llamada=self.controles.timeout_por_llamada_segundos,
            cuotas=self.cuotas,
            espacio_vigente=lambda: (
                self.store.obtener_workspace(workspace_id) is not None
                and (
                    not trabajo.get("generation_id")
                    or self.store.obtener_generacion(trabajo["generation_id"]) is not None
                )
            ),
            reintentos_transitorios=self.controles.reintentos_transitorios,
            base_backoff_segundos=self.controles.base_backoff_segundos,
            registrar_reintento=lambda: self.store.actualizar_job(job_id, JobStatus.RUNNING, incremento_intentos=1),
            _evento_cancelacion=evento_cancelacion,
        )

        try:
            contexto.chequear()
            resultado = funcion(contexto)
            with self._lock:
                contexto.chequear()
                self._finalizar(job_id, JobStatus.COMPLETED, resultado=_serializar(resultado))
            self._limpiar(job_id)
            return
        except RechazoCalidadError:
            self._finalizar(job_id, JobStatus.REJECTED_QUALITY, "El contenido no superó la revisión de calidad.")
            return
        except TrabajoCanceladoError:
            self._finalizar(job_id, JobStatus.CANCELLED, "Cancelado por el usuario durante la ejecución.")
            self._limpiar(job_id)
            return
        except DeadlineExcedidoError:
            self._finalizar(
                job_id,
                JobStatus.FAILED,
                f"El trabajo excedió el deadline de {self.controles.deadline_ejecucion_segundos:.0f} s de ejecución.",
                error_code="DEADLINE",
            )
            self._limpiar(job_id)
            return
        except CuotaAgotadaError as error:
            # "La cuota diaria agotada detiene nuevas llamadas": falla el
            # trabajo con causa técnica, sin ciclar reintentos (§7.5).
            self._finalizar(job_id, JobStatus.FAILED, str(error), error_code="CUOTA_AGOTADA")
            self._limpiar(job_id)
            return
        except ReintentableError:
            self._finalizar(
                job_id,
                JobStatus.FAILED,
                "Falló una operación transitoria; no se repite el trabajo completo.",
                error_code="REINTENTOS_AGOTADOS",
            )
            return
        except Exception:  # noqa: BLE001 - el worker nunca debe morir
            # Error PERMANENTE: sin reintento (reintentar validaciones no
            # tiene sentido) y sin tragar la causa: queda en el registro.
            self._finalizar(job_id, JobStatus.FAILED, "Error interno del trabajo.", error_code="ERROR_TRABAJO")
            self._limpiar(job_id)
            return

    def _finalizar(
        self,
        job_id: str,
        status: JobStatus,
        mensaje: str | None = None,
        error_code: str | None = None,
        resultado: str | None = None,
    ) -> None:
        with self._lock:
            self.store.finalizar_job(job_id, status, error_code=error_code, error_message=mensaje, resultado=resultado)
            self._limpiar(job_id)

    def _limpiar(self, job_id: str) -> None:
        # Ejecutable y flag ya no hacen falta al llegar a estado terminal.
        self._ejecutables.pop(job_id, None)
        self._cancelaciones.pop(job_id, None)


def _serializar(resultado: Any) -> str | None:
    """Convierte el resultado del trabajo a JSON para persistirlo.

    Los objetos Pydantic (p. ej. el paquete de #31) saben serializarse
    solos; lo demás cae en el default=str para no perder el resultado por
    un tipo exótico.
    """
    if resultado is None:
        return None
    if hasattr(resultado, "model_dump_json"):
        return resultado.model_dump_json()
    return json.dumps(resultado, default=str, ensure_ascii=False)
