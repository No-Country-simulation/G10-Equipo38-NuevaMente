# Marco de gobernanza de datos de salud

> **Documento demo — contenido SIMULADO con ejemplos ficticios.** Este marco usa una red de salud imaginaria («Red Salud Virtual») para ilustrar políticas de gobernanza de datos clínicos. **No contiene ningún dato personal real**: personas, hospitales, identificadores y casos son inventados para fines educativos. Este material no constituye asesoramiento legal, clínico ni regulatorio.
> **Proyecto**: NuevaMente · **Versión**: v1 · **Nicho**: Salud

---

## 1. Propósito y alcance

La gobernanza de datos de salud define quién puede acceder a qué información clínica, con qué propósito, durante cuánto tiempo y con qué controles. Este marco cubre los datos que la Red Salud Virtual administra en tres sistemas: la historia clínica electrónica (HCE), el sistema de turnos y el depósito analítico de indicadores.

Alcance explícito: datos de pacientes ficticios (identificables y seudonimizados), datos operativos (turnos, camas) y datos agregados de gestión. Queda fuera el pago/prestadORES (finanzas puras), que se rige por el marco financiero de la organización.

## 2. Clasificación de los datos

Toda tabla y todo reporte hereda la clasificación de su dato más sensible:

| Clase | Definición | Ejemplo ficticio | Requisito mínimo |
|---|---|---|---|
| C4 — Clínica identificable | Dato que identifica y describe salud | HCE de la paciente «ANA-0001» (34 años, hipotiroidismo) | Acceso por rol + propósito registrado + cifrado en tránsito y reposo |
| C3 — Clínica seudonimizada | Identificador opaco + dato clínico | Registro `PAC-0417` con hemoglobina 13.1 g/dL | Acceso por rol para análisis aprobado; prohibida la reidentificación |
| C2 — Operativa | Procesos sin dato clínico directo | Turnos, ocupación de camas, demoras de guardia | Acceso interno autenticado |
| C1 — Agregada | Métricas no reversibles | «El 62% de los turnos de cardiología se registra en la mañana» | Publicable internamente; publicación externa previa revisión |

Regla de oro: ante la duda, se clasifica en el nivel superior. Bajar de clase exige un análisis firmado del responsable de datos, no una decisión de último momento.

## 3. Roles y responsabilidades

| Rol | Responsabilidad principal | Ejemplo de decisión que le corresponde |
|---|---|---|
| Responsable de datos | Clasificación, retención y aprobación de usos nuevos | Autorizar (o no) un modelo predictivo sobre datos C3 |
| Delegado de protección | Supervisión de derechos del paciente y incidentes | Resolver la solicitud de acceso de «ANA-0001» a su HCE |
| Custodio técnico | Controles de acceso, cifrado y registros | Implementar el log de auditoría inmutable de accesos C4 |
| Analista | Uso según propósito aprobado | Ejecutar el tablero de readmisiones sin datos identificables |
| Auditoría interna | Verificación periódica del cumplimiento | Muestrear accesos C4 del último trimestre y validar causalidad |

Principio de mínimo privilegio: cada rol accede a la clase más baja que le permita cumplir su tarea. El analista trabaja por defecto con C3/C1; el acceso a C4 es excepcional, temporario y registrado.

## 4. Ciclo de vida y retención

| Tipo de dato | Retención base (política ficticia) | Destino final |
|---|---|---|
| Historia clínica electrónica | 20 años desde el último episodio | Archivo cifrado; destrucción certificada al vencer |
| Turnos y agenda | 24 meses | Agregación a C1 y borrado del detalle |
| Logs de acceso a datos C4 | 36 meses | Archivo inmutable para auditoría |
| Datos seudonimizados de estudios | Duración del estudio + 12 meses | Destrucción de la tabla de reidentificación al cierre |
| Reportes agregados | Sin límite (C1) | Publicación interna |

El borrado seguro incluye derivados: copias de trabajo, extractos analíticos y cachés. La política prohíbe exportar C4 a hojas de cálculo; los casos docentes se publican con el proceso de seudonimización del apartado 6.

## 5. Consentimiento y finalidad

Cada uso de datos clínicos se ampara en una finalidad registrada antes del acceso. La Red Salud Virtual distingue tres:

1. **Asistencia**: la que motivó el registro del dato. El equipo tratante de «ANA-0001» accede a su HCE durante el episodio de cuidado.
2. **Gestión**: indicadores agregados para operar la red. Nunca requiere dato identificable; si un tablero puede construirse en C1, construirlo en C3 es una desviación.
3. **Investigación**: exige proyecto aprobado, datos seudonimizados por defecto y reidentificación solo con autorización expresa del responsable de datos.

Una finalidad no habilita otra: el acceso asistencial no autoriza a alimentar un modelo predictivo, y un proyecto de investigación vencido no prorroga el acceso por silencio.

## 6. Seudonimización y anonimización (ejemplos ficticios)

Ejemplo de transformación controlada de un registro C4 a C3:

| Campo | Original ficticio (C4) | Transformado (C3) |
|---|---|---|
| Nombre | Ana Pérez | (eliminado) |
| Documento | 30.145.222 | `PAC-0417` (correlación en bóveda aparte) |
| Fecha de nacimiento | 12/03/1992 | 1992 (año) |
| Diagnóstico | Hipotiroidismo subclínico | Hipotiroidismo subclínico (se conserva: es el objeto de análisis) |
| Código postal | C1425 | C14 (dígito de barrio eliminado) |

Advertencia metodológica: un conjunto «seudonimizado» puede reidentificarse por combinación de campos débiles (año + código postal + diagnóstico infrecuente). Antes de liberar un extracto, el custodio ejecuta la prueba de k-anonimato con k ≥ 5: si algún registro es distinguible entre menos de 5 personas, se generaliza otro campo.

La anonimización total (irreversible) solo la certifica el responsable de datos con informe técnico; mientras no exista, el dato sigue siendo personal y sigue rigiendo todo este marco.

## 7. Controles técnicos mínimos

1. Acceso por rol con aprobación del custodio y vigencia revisada cada 90 días.
2. Registro inmutable de accesos a C4: quién, qué registro, cuándo y con qué finalidad declarada.
3. Cifrado en tránsito (TLS) y en reposo para C3 y C4.
4. Separación de la bóveda de reidentificación: llaves en sistema aparte, con doble autorización para usarlas.
5. Segmentación de red: el depósito analítico no tiene salida a Internet.
6. Pruebas de restauración de backups semestrales documentadas con fecha y responsable.

## 8. Incidentes y respuesta

Ante sospecha de acceso indebido a datos C4: contener (revocar accesos), preservar evidencia (logs sellados), evaluar riesgo al paciente y notificar según la política interna y la regulación aplicable, en los plazos que esta fije. La lección aprendida cierra el incidente con un cambio de control verificable, no con un memorando.

Métrica de madurez: tiempo medio entre detección y contención. La red ficticia fija como objetivo < 24 horas para C4 y reporta su valor real — no un objetivo — en el comité trimestral.

## 9. Indicadores del programa de gobernanza

| Indicador | Definición | Meta ficticia |
|---|---|---|
| Cobertura de clasificación | % de tablas con clase asignada | 100% |
| Accesos C4 con finalidad válida | % de accesos con propósito registrado y coherente | ≥ 99% |
| Extractos analíticos solo C3/C1 | % de datasets aprobados sin identificables | 100% |
| Restauración de backups probada | % de restauraciones semestrales exitosas y documentadas | 100% |
| Tiempo de contención de incidentes C4 | Mediana detect→contener | < 24 h |

## 10. Preguntas de autoevaluación

1. ¿Puede un analista acceder a la HCE de «ANA-0001» para corregir un tablero? — No: el tablero se corrige con datos C3/C1; el acceso asistencial es del equipo tratante.
2. ¿Qué exige liberar un extracto de investigación? — Proyecto aprobado, transformación del apartado 6 y prueba k ≥ 5.
3. ¿Cuándo vence el silencio como prórroga? — Nunca: la finalidad vencida corta el acceso.
4. ¿Quién certifica una anonimización? — El responsable de datos, con informe técnico.
