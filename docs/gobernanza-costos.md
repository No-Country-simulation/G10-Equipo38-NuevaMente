# Presupuestos y ciclo de vida (#50)

Los límites de aplicación base son 1.000.000.000 bytes de objetos y 20.000 solicitudes
mensuales en OCI, junto con los límites de recursos por espacio: por defecto cinco
documentos y veinte generaciones conservadas. Las cuotas de OCI pueden reducirse en
`OCI_MAX_STORAGE_BYTES` y `OCI_MAX_REQUESTS_PER_MONTH` (nunca ampliarse). Los límites
por espacio son configurables al arrancar el servicio mediante `WORKSPACE_MAX_DOCUMENTS`
(default: 5, rango 1..100) y `WORKSPACE_MAX_GENERATIONS` (default: 20, rango 1..500).

Los techos defensivos (100 documentos y 500 generaciones) acotan el uso de memoria RAM
(VM de 12 GB), la indexación en ChromaDB y las consultas de SQLite. La configuración
se lee al arrancar (requiere reinicio del servicio para aplicarse; no hay recarga en caliente).
Aumentar un límite amplía la capacidad de retención. Reducir un límite por debajo de los
recursos ya almacenados no purga ni destruye datos existentes; bloquea nuevas admisiones
(HTTP 429 `RATE_LIMITED`) hasta que el usuario libere plazas borrando recursos.
Conservar más recursos en el espacio no consume tokens de Gemini por sí solo: el gasto
de modelos depende de nuevas ingestas, generaciones y operaciones ejecutadas. Las cuotas OCI,
la retención de 30 días y los límites de archivo (20 MB / 100 páginas / 100k tokens)
permanecen invariantes. La reserva de recursos ocurre en la misma transacción que la
intención/idempotencia: una repetición no consume plazas, una lápida libera su plaza y dos
solicitudes concurrentes no pueden sobrepasar el límite.

El presupuesto de solicitudes se amplió el 15/10/2026 de 5.000 a 20.000 con
autorización del responsable del proyecto. Los contadores no se reinician:
18.000 quedan disponibles para operaciones y 2.000 se reservan para limpieza.
Es un límite preventivo de NuevaMente; Oracle publica 50.000 solicitudes/mes
para Object Storage Always Free. El consumo externo de la tenancy se verifica
por separado: [asignaciones oficiales](https://www.oracle.com/cloud/free/).

El ledger SQLite `oci_budget.sqlite3` incluye inventario, reservas inciertas y
contador mensual por operación SDK. Cada lectura, listado, intento de escritura,
borrado y retry se reserva antes de ir a OCI. El mock de la aplicación usa el mismo
ledger, con rótulo `mock`; no acredita conectividad real. Un reemplazo confirmado
contabiliza su tamaño nuevo. Las reservas ambiguas permanecen hasta reconciliación;
no se consideran espacio disponible por un timeout.

La alerta aparece al 80%. Se reservan el 10% de solicitudes y el 1% de capacidad
para revocación y limpieza (redondeados hacia abajo en presupuestos pequeños).
La actividad ordinaria se bloquea antes de consumir ese margen. Mantenimiento
puede usarlo hasta el límite total; al agotarlo quedan obligaciones pendientes
que se reintentan tras renovar cuota. Una alerta OCI de facturación no corta gasto.
Las lecturas de control del arranque pueden usar ese margen para que un reinicio
al techo ordinario permita iniciar la limpieza; ninguna nueva escritura de usuario
obtiene ese privilegio.

## Información al usuario

Contraste documental del **17/10/2026**, base integrada `5988244`: básico y
multiagente comparten admisiones, presupuestos y controles de persistencia.
El básico no garantiza ahorro fijo ni aprobación independiente; recuperación
y descargas conservan el modo del paquete 1.0/1.1. Descargar no llama al LLM,
pero las lecturas y el almacenamiento de derivados consumen presupuesto OCI.
No se reinician contadores al cambiar modo, versión o lector. Este contraste no
repite la inspección real histórica ni acredita el consumo actual.

`GET /api/workspaces/current` requiere Bearer y devuelve `workspace_id`,
`expira_en`, `ultima_actividad_en`, `uso` y `almacenamiento` (agregados, proveedor,
límites, margen, alerta y bloqueo). No expone claves de objetos, credenciales ni
diagnósticos de otros espacios. El sidebar muestra fecha/uso/alerta en es/en/pt.
Un límite de recursos responde 429 `RATE_LIMITED`,
`details.motivo=workspace_resource_limit`. El presupuesto de storage conserva
503 `STORAGE_UNAVAILABLE` del contrato existente. El acceso a un espacio retirado
continúa denegado aun cuando su purga falle.

La actividad válida renueva 30 días de retención (agrupada cada cinco minutos
para reducir escrituras). No renueva un espacio ya vencido. El sincronizador
existente marca los vencidos, revoca sesiones y reintenta limpieza cada 60 s.
Espera productores activos, confirma la lápida remota, retira Chroma y todos los
prefijos de originales/outputs/exports/progreso, y purga contenido operativo local.
Mantiene el manifiesto de revocación durante el plazo de lápidas, y después lo
elimina. Un volumen reconstruido respeta las lápidas y la expiración. Si el disco
se pierde antes de confirmar remotamente una revocación durante una caída de OCI,
la marca sólo local no puede recuperarse: conservar el volumen hasta sincronizar.

Los aprobados para `/persist` vencen como máximo en 24 h; la purga ya existente
se ejecuta también en cada mantenimiento. Temporales de parser/exportadores
usan contextos con cierre/limpieza aun en cancelación o excepción.

## Inspección y observabilidad administrativa

El arranque real valida bucket privado Standard, versionado y replicación
desactivados, auto-tiering desactivado y ausencia de transiciones de clase
habilitadas en la política de ciclo de vida. No modifica configuraciones cloud.

```bash
PYTHONPATH=backend python -m app.tools.inspect_storage --env-file .env
PYTHONPATH=backend python -m app.tools.inspect_storage --env-file .env --local
```

La primera inspección usa sólo lecturas del SDK y muestra controles verificados y
presupuesto. La segunda abre `operativo.db` en modo sólo lectura y muestra estados
`pendiente|datos_purgados|completada`, intentos y códigos de error de limpieza, sin
tokens ni cuerpos. No exponer esta salida administrativa por API pública.

En una tenancy con permiso para Budgets, `app.tools.oci_budget_rule.asegurar_regla`
crea/reutiliza un budget del compartimento y una regla ACTUAL al 80%, sin modificar
reglas existentes ni IAM. OCI exige un monto mínimo de 1; es un umbral de observación,
sin autorizar gasto. Si se desean correos, configurar explícitamente el destinatario
en la consola OCI. La regla no incorpora destinatarios de manera implícita.
La alerta cubre facturación agregada; no sustituye estos límites ni garantiza
Always Free. Si faltan permisos, registrar esa limitación en evidencia del despliegue.
La ejecución real autorizada del #50 y sus resultados están en
[Evidencia OCI](evidencia-oci-gobernanza.md).
Ese registro del **12/10/2026** conserva el límite anterior de 5.000 solicitudes
y su margen de 500; no describe los límites actuales de 20.000/2.000 ni acredita
el estado actual de la tenancy. Esta actualización documental no repite la inspección.

El ledger `oci_budget.sqlite3` y las obligaciones locales de limpieza/revocación
forman parte del estado durable del volumen. Recuperar originales y manifiestos
desde OCI no reconstruye el contador mensual perdido ni las marcas que nunca
se sincronizaron. No interpretar un ledger nuevo como consumo cero. El
[mapa de #58](verificacion-issue-58.md#recuperación-y-estado-durable) registra estos
límites; el [procedimiento de backup/restauración](despliegue-oci.md#16-backup-físico-consistente-del-volumen-completo)
está redactado, con ensayo sintético limitado. Su reproducción operativa real sigue
pendiente en #133; frecuencia, retención y custodia requieren decisión del equipo.

Referencias de verificación backend existentes: `backend/tests/test_budgets.py`
y `backend/tests/test_budgets_api.py`, además de la inspección real anterior.
El [módulo UI histórico, base f292415](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/frontend_streamlit_viejo/tests/test_budgets_ui.py)
fue retirado con Streamlit en #139 y no acredita React ni es ejecutable en esta base.
Son referencias de verificación existentes, no pruebas ejecutadas por esta
corrección documental. La aceptación externa del despliegue permanece en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
