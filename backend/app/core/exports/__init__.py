"""Exportadores pedagógicos multiformato (carril API/QAD).

Módulos previstos (§15):
- router.py: router de formatos de exportación (issue #43).
- markdown.py: renderizado Markdown didáctico (issue #43).
- pdf.py: PDF didáctico con ReportLab (issue #44).
- anki.py: mazos Anki con genanki + CSV/TSV (issue #45).

Seguridad de exportación (§11.4): escape de separadores/HTML en CSV/TSV,
celdas que no actúen como fórmulas de planilla y snippets presentados para
lectura, nunca ejecutados.
"""
