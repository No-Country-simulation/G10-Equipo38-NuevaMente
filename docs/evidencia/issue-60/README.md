# Evidencia del #60 — preparación en draft

Estado al 15 de octubre de 2026: **pendiente**. El PR implementa la preparación y
comparación automatizable; el equipo decidió dejar la evaluación en draft hasta
incorporar los materiales reales y las anotaciones humanas.

- `casos.json`: matriz de 12 casos, tres idiomas, cuatro perfiles por idioma,
  ≥3 formatos por idioma, cinco formatos globales y tres documentos demo, con hashes.
  La versión 1.1.0 usa el PDF VCN v2 corregido en el PR #132.
- `plantilla-revisor-a.json` / `plantilla-revisor-b.json`: vacías y sin confirmación
  humana. Al disponer de material, `prepare` genera las filas con su SHA-256 exacto.
- `reporte-provisional.json` / `.md`: salida efectiva del comparador sin materiales
  reales ni notas humanas; muestra comparable 0, métricas nulas y bloqueos explícitos.

La prueba de captura recorre 12 generaciones con la API, el grafo, Chroma y storage
reales, usando Gemini/OCI simulados. Sus paquetes y notas ficticias solo viven en
los tests; no se publican como resultados de evaluación pedagógica.

## Pasos pendientes para el equipo

1. Obtener/capturar las 12 adaptaciones con Gemini real y cuotas configuradas,
   guardando configuración, versiones y referencias; verificar el modo del backend.
2. Preparar los materiales y entregar las planillas a dos personas identificadas
   que revisen de forma independiente ES/EN/PT.
3. Registrar notas y motivos; resolver desacuerdos sin borrar las anotaciones originales.
4. Analizar falsos aprobados/rechazados; corregir y reevaluar los falsos aprobados,
   conservando el artefacto original, commit, nueva versión y dos nuevas notas.
5. Archivar el reporte medido, la evidencia pública sin secretos y la revisión final del equipo.

## Avance realizado

- Rama actualizada con `develop`; conserva la implementación del draft #119.
- Matriz 1.1.0 fijada a VCN v2, sin ampliar los doce casos ni cambiar umbrales.
- `prepare` incluye copias verificadas de los tres originales y la matriz. Los
  doce materiales pendientes conservan sus objetivos y parámetros; no contienen
  resultados inventados. Las dos planillas siguen vacías.
- `historico/matriz-1.0.1.json` conserva la matriz anterior.
- [Caso real histórico de VCN v1](historico/vcn-v1/README.md): paquete aprobado del
  #55 importado sin reescribir su contenido, con fuente exacta y material sin la
  decisión automática. No se cuenta como uno de los doce casos de v2.

Integrar primero la corrección de fuente del **PR #132**. Después, preparar los
documentos privados de v2 con sus hashes y ejecutar/capturar los doce casos con
presupuesto disponible. La precarga y los ejemplos públicos no cambian al editar
el PDF en Git. No se lanzaron solicitudes reales nuevas para este avance.

No hay anotaciones humanas ni medición comparativa de calidad todavía. La CI verifica las
herramientas y sus guardas; el #60 se mantiene abierto. Procedimiento y comandos:
[evaluacion-pedagogica.md](../../evaluacion-pedagogica.md).
