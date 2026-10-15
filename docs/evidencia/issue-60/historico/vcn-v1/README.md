# Caso histórico real: VCN v1

Este material proviene del escenario A del [PR #130](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/130),
generado y aprobado con Gemini real y persistido en OCI. No fue redactado a mano.
La envoltura de evaluación se construyó desde el JSON canónico recuperado en el
smoke #54; `procedencia.json` conserva sus identificadores y hashes.

- `es-principiante-r1.json`: artefacto de auditoría con la evaluación automática.
- `material-revisor.json`: contenido para una revisión independiente, sin
  decisión, score ni trazas del juez. Compartirlo sin el archivo anterior antes
  de una primera anotación humana.
- `documents/redes_vcn_oci.pdf`: bytes exactos del original usado, comprobados
  contra el hash del paquete. Se recuperaron del commit histórico aplicando la
  conversión de saltos de línea de Windows que existía entonces; no es la fuente v2.

La tarjeta sobre subred pública conserva un error de la fuente v1 registrado en
#117. Permite analizar cómo una adaptación fiel puede seguir siendo
técnicamente incorrecta. Es un **candidato para revisión humana**, sin notas ni
adjudicación: no se declara un falso aprobado consolidado ni se oculta el error.

El comparador identifica este hash como versión anterior y lo excluye de la
muestra vigente. La matriz actual exige VCN v2. No modificar los hashes para
reutilizar el resultado como si hubiera sido generado desde la fuente corregida.
