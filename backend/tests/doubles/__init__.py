"""Dobles de prueba (test doubles) compartidos por toda la suite.

Un "doble" es una versión falsa y controlable de una dependencia externa:
devuelve exactamente lo que el test programa, sin red, sin cuota y sin
demora. Viven bajo backend/tests/ (no en app/) porque son infraestructura
de testing del carril QAD, no código de producción.
"""
