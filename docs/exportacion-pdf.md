# PDF didáctico (#44)

`GET /api/exports/{generation_id}?format=pdf` devuelve `application/pdf` y un
archivo `{generation_id}.pdf`. Necesita sesión y una generación `completed`
autorizada. Conserva los controles de caché, aislamiento, integridad, revocación
y borrado del router del #43 y los presupuestos del #50.

El PDF se deriva del paquete canónico aprobado, sin IA ni accesos externos.
Se prepara y confirma en storage antes de `completed`; un fallo deja el aprobado
retenido para `/persist`, que reintenta sin regenerar. Una generación anterior
sin PDF lo crea desde el canónico en la primera descarga. El botón de la UI
corresponde al #46.

## Presentación

- A4, fondo blanco, márgenes de 20/22 mm, encabezado, número de página, tinta
  oscura, líneas finas y acento `#5e6ad2`, adaptados del design system.
- Título, audiencia, idioma, tiempo orientativo, objetivos, prerrequisitos,
  conceptos, contenido, identificación de fuente y referencias numeradas.
- Flashcards: tabla imprimible con frente y dorso identificados. Las tablas
  extensas continúan por páginas repitiendo sus encabezados.
- Quiz: opciones sin marcas/clave ni justificación en los enunciados; todas las
  soluciones comienzan en una página nueva, como última sección.
- Tutorial: pasos, código, resultado esperado y verificación. Bloques cortos
  pasan enteros a la página siguiente; los extensos se dividen por líneas.
  La numeración indica la línea original y `+` identifica una continuación
  visual (línea larga o bloque), sin perder contenido ni reducir la letra.
- Resumen ejecutivo: puntos, tabla de impacto/implicaciones y acciones.
- Guion: escenas con duración, narración en itálica, diapositivas e interacción.

Las etiquetas siguen el idioma ES/EN/PT del contenido. Los textos se normalizan
a NFC para presentar acentos compuestos y se escapan antes de `Paragraph`:
HTML, imágenes y enlaces del documento se imprimen literalmente. Sólo la
plantilla crea enlaces internos a referencias; no genera enlaces externos,
acciones JavaScript ni ejecuta los bloques de código.

Fuentes Unicode TTF locales y licencias: [fonts](../backend/app/exports/fonts/README.md).
No hay dependencia de red ni rutas de fuentes del host. `ReportLab==5.0.1`
es la misma versión ya fijada para documentación, ahora también de runtime.
`invariant=1` está configurado en `BaseDocTemplate`: texto, paginación y bytes
son estables, necesarios para comparar la caché con la vista aprobada.

## Verificación reproducible

```bash
pytest backend/tests/test_exports_pdf.py backend/tests/test_exports_pdf_api.py
python .github/scripts/verify_pdf_exports.py
```

El script produce cinco fixtures VCN tipados y sus páginas PNG con PDFium y
Poppler (`pdftoppm` requerido), bajo `.data/pdf-evidence/`. Verifica apertura,
conteo de páginas, texto dentro del papel y determinismo. El artifact de CI
`pdf-export-evidence` conserva PDFs, imágenes y reporte por 14 días.
Los fixtures son evidencia de maquetación; no acreditan una corrida Gemini
ni una evaluación pedagógica real, cubiertas por #54 y #60.

Las pruebas incluyen los cinco formatos en tres idiomas, fuentes embebidas,
tablas mayores que una página, código de 130 líneas y una línea de 1.000
caracteres, XML malicioso literal, separación del quiz, concurrencia, caché
alterada, headers HTTP, aislamiento y recuperación tras fallo de persistencia.

Revisión visual realizada el 12/10/2026 sobre las siete páginas de estas cinco
muestras: PDFium y Poppler las abrieron y renderizaron; también se inspeccionaron
en PDF.js. Se verificaron frente/dorso, soluciones en la última página,
indentación del código, tablas, referencias y acentos portugueses sin glifos
vacíos. Las muestras breves ocupan 2/2/1/1/1 páginas respectivamente. Los casos
extensos se ejercitan aparte en los tests para comprobar las continuaciones.
