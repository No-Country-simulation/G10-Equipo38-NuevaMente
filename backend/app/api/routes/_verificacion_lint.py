"""ARCHIVO TEMPORAL DE VERIFICACION DE LA CI (issue #02). SE ELIMINA EN ESTE MISMO PR.

Proposito: demostrar el criterio de aceptacion "un PR que rompe formato o
lint queda bloqueado". Este archivo importa un modulo que no usa: la regla
F401 de ruff (import sin uso) hace fallar el paso "Linter (ruff check)" del
workflow .github/workflows/ci.yml, y como develop exige el check
"calidad (3.11)", el merge queda bloqueado hasta corregirlo.

No pertenece al proyecto: es el error intencional que pide la verificacion
del issue ("abrir un PR de prueba con un error de lint intencional y verlo
fallar; corregir y verlo pasar").
"""

import os  # <- ERROR INTENCIONAL: import sin uso (ruff F401).
