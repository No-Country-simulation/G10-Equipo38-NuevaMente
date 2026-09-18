"""Sesiones anónimas con recuperación (carril API).

Módulo previsto (§15): manager.py (issue #09): workspaces anónimos, código de
recuperación y ciclo de vida de sesión (SESSION_MAX_HOURS=24 y
WORKSPACE_RETENTION_DAYS=30 del Apéndice A).

Reglas de seguridad (§11.2): los códigos de recuperación y tokens no se
escriben en logs ni se envían al LLM; recuperar el historial/progreso tras
perder la sesión es criterio crítico 5 de §12.2.
"""
