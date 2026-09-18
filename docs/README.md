# 📚 Documentación del Proyecto

| Documento | Qué contiene | Cuándo usarlo |
|---|---|---|
| [`plan-implementacion.md`](plan-implementacion.md) | **Empezar acá.** Plan maestro: estrategia, 7 fases, 6 carriles, tablero de 60 issues con dependencias, trazabilidad a requisitos, capacidad, hitos y riesgos | Organizar y seguir el trabajo del equipo |
| [`issues/fase-0-fundaciones.md`](issues/fase-0-fundaciones.md) → [`issues/fase-6-demo-entrega.md`](issues/fase-6-demo-entrega.md) | Detalle de cada issue (objetivo, tareas, criterios de aceptación, dependencias, verificación), listo para copiar a GitHub | Al crear los issues y al tomar trabajo |
| [`arquitectura.md`](arquitectura.md) | Diagramas del sistema: contexto, contenedores, flujo E2E, RAG, grafo multi-agente, fidelidad, persistencia y despliegue | Onboarding, diseño y base del README final |
| [`contratos-api.md`](contratos-api.md) | Contrato API v1: rutas, envolvente de errores, enums, SSE, `PedagogicalOutput` con ejemplo completo | Contrato compartido backend/frontend; cambios = `contract-change` |
| [`guia-trabajo-equipo.md`](guia-trabajo-equipo.md) | Git flow, conventional commits, PRs, tablero, ceremonias y reglas de convivencia entre carriles | Desde el día 1 y en cada PR |
| [`demo-escenarios.md`](demo-escenarios.md) | Escenarios A/B/C, guion minuto a minuto, checklist de evidencias, contingencias | Al ensayar y presentar la entrega |
| [`../decisiones_proyecto.md`](../decisiones_proyecto.md) | **Documento padre**: todas las decisiones de diseño con su justificación | Referencia normativa de todo lo demás |

## Mantenimiento

- La trazabilidad va siempre en este orden: **cambio de decisión → `decisiones_proyecto.md` → este plan/issues → código**.
- Los contratos (`contratos-api.md` + schemas) solo cambian por PR `contract-change` con revisión multi-carril.
- La evidencia de ejecución (capturas, JSON, videos) se archiva en `docs/evidencia/` conforme avanzan `Issue 54`, `Issue 55` y `Issue 56`.
