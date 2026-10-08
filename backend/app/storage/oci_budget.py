"""Reservas persistentes OCI, separadas de Gemini; auditoría ampliada en #50."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.storage.provider import StorageBudgetExceeded, StorageUnavailable


class PresupuestoOCI:
    def __init__(self, ruta: Path, *, solicitudes: int = 5000, bytes_maximos: int = 1_000_000_000):
        if not 1 <= solicitudes <= 5000 or not 1 <= bytes_maximos <= 1_000_000_000:
            raise ValueError("El presupuesto OCI solo puede reducir los límites de §8.4")
        self.ruta, self.solicitudes, self.bytes_maximos = Path(ruta), solicitudes, bytes_maximos
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.ruta) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS solicitudes (mes TEXT PRIMARY KEY, usadas INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS objetos (nombre TEXT PRIMARY KEY, bytes INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS reservas (token TEXT PRIMARY KEY, nombre TEXT NOT NULL, bytes INTEGER NOT NULL);
                """)
        except sqlite3.Error:
            raise StorageUnavailable("No se pudo abrir el presupuesto persistente OCI") from None

    @contextmanager
    def _transaccion(self):
        db = None
        try:
            db = sqlite3.connect(self.ruta, timeout=30)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error:
            raise StorageUnavailable("El presupuesto OCI no está disponible; no se enviará la solicitud") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _mes():
        return datetime.now(timezone.utc).strftime("%Y-%m")

    @staticmethod
    def _bytes(db):
        return sum(
            db.execute(f"SELECT COALESCE(SUM(bytes), 0) FROM {tabla}").fetchone()[0]
            for tabla in ("objetos", "reservas")
        )

    def reservar_solicitud(self):
        with self._transaccion() as db:
            mes = self._mes()
            fila = db.execute("SELECT usadas FROM solicitudes WHERE mes=?", (mes,)).fetchone()
            usadas = fila[0] if fila else 0
            if usadas >= self.solicitudes:
                raise StorageBudgetExceeded("Presupuesto mensual de solicitudes OCI agotado")
            db.execute(
                "INSERT INTO solicitudes VALUES (?, ?) ON CONFLICT(mes) DO UPDATE SET usadas=excluded.usadas",
                (mes, usadas + 1),
            )

    def incorporar_inventario(self, objetos):
        with self._transaccion() as db:
            for nombre, tamanio in objetos:
                if not isinstance(tamanio, int) or tamanio < 0:
                    raise StorageUnavailable("El inventario OCI no incluye tamaños válidos")
                db.execute(
                    "INSERT INTO objetos VALUES (?, ?) ON CONFLICT(nombre) DO UPDATE SET bytes=MAX(bytes,excluded.bytes)",
                    (nombre, tamanio),
                )

    def reservar_upload(self, token, nombre, tamanio):
        with self._transaccion() as db:
            if self._bytes(db) + tamanio > self.bytes_maximos:
                raise StorageBudgetExceeded("Presupuesto de capacidad OCI agotado")
            db.execute("INSERT INTO reservas VALUES (?, ?, ?)", (token, nombre, tamanio))

    def confirmar_upload(self, token, nombre, tamanio):
        with self._transaccion() as db:
            db.execute(
                "INSERT INTO objetos VALUES (?, ?) ON CONFLICT(nombre) DO UPDATE SET bytes=MAX(bytes,excluded.bytes)",
                (nombre, tamanio),
            )
            db.execute("DELETE FROM reservas WHERE token=?", (token,))

    def liberar_reserva(self, token):
        with self._transaccion() as db:
            db.execute("DELETE FROM reservas WHERE token=?", (token,))

    def confirmar_borrado(self, nombre):
        with self._transaccion() as db:
            db.execute("DELETE FROM objetos WHERE nombre=?", (nombre,))

    def resumen(self):
        with self._transaccion() as db:
            fila = db.execute("SELECT usadas FROM solicitudes WHERE mes=?", (self._mes(),)).fetchone()
            solicitudes = fila[0] if fila else 0
            tamanio = self._bytes(db)
            pendientes = db.execute("SELECT COUNT(*) FROM reservas").fetchone()[0]
            return dict(
                mes=self._mes(),
                solicitudes=solicitudes,
                limite_solicitudes=self.solicitudes,
                bytes_reservados=tamanio,
                limite_bytes=self.bytes_maximos,
                reservas_inciertas=pendientes,
                alerta=solicitudes >= self.solicitudes * 0.8 or tamanio >= self.bytes_maximos * 0.8,
            )
