"""Catálogos i18n ES/EN/PT de la interfaz (§17, issue #06) + helper t().

Qué es este paquete: el ÚNICO lugar donde viven los textos visibles de la
UI en los tres idiomas del proyecto (español latinoamericano por defecto,
inglés técnico y portugués pt-BR). Los componentes Streamlit (issue #15 en
adelante) llaman a ``t("quiz.correcto", idioma_ui)`` en vez de escribir
cadenas a mano — así no existe texto duplicado ni idiomas mezclados.

Regla central (§17.3): los CÓDIGOS de máquina viajan por la API y son
estables; las ETIQUETAS traducidas viven aquí. Ejemplo: el backend
responde ``status: "rejected_quality"`` y la UI muestra
``t("estados.trabajo.rejected_quality")`` → «Rechazado por calidad».

Convención de claves por área (documentada también en cada catálogo):

    comun.*      botones y términos transversales
    nav.*        navegación principal
    onboarding.* espacio anónimo y código de recuperación
    sidebar.*    panel de parámetros de generación
    upload.*     carga de documentos
    generar.*    disparo y seguimiento de generaciones
    estados.*    etiquetas humanas de estados de trabajo/documento
    perfil.* formato.* nicho.* detalle.* idioma.*   etiquetas de los enums
    quiz.* chat.* glosario.* progreso.* exportar.* historial.*
    errors.*     un mensaje por código ESTABLE de la API (§7.3)
    a11y.*       textos de accesibilidad (§12.4)
    almacenamiento.*  rótulos de proveniencia del storage (§8.2)

Los placeholders van entre llaves (``{posicion}``) y se rellenan con
``format``: ``t("generar.posicion", "es").format(posicion=3)``.

Idioma de UI ≠ idioma de contenido (§17.1): cambiar el selector de la UI
no regenera materiales; el idioma del contenido es el parámetro
``idioma_salida`` del contrato.
"""

from __future__ import annotations

# Imports RELATIVOS (".es" = "el módulo es.py que vive al lado de este
# archivo"): funcionan igual sin importar cómo se haya encontrado el
# paquete (como "i18n" con frontend/ en el syspath de pytest.ini).
from .en import CATALOGO_EN
from .es import CATALOGO_ES
from .pt import CATALOGO_PT

# Idiomas soportados por la UI, con su catálogo. "es" es el default (§17.1).
CATALOGOS: dict[str, dict[str, str]] = {
    "es": CATALOGO_ES,
    "en": CATALOGO_EN,
    "pt": CATALOGO_PT,
}

IDIOMA_POR_DEFECTO = "es"


def t(clave: str, idioma: str = IDIOMA_POR_DEFECTO) -> str:
    """Devuelve el texto de `clave` en `idioma`, con fallback a español.

    Reglas (las pide el issue #06):

    1. Si la clave existe en el idioma pedido, se usa esa traducción.
    2. Si NO existe (traducción olvidada), se devuelve la española en su
       lugar: el usuario ve texto en otro idioma antes que una clave cruda.
       El olvido igualmente explota en el test de paridad, que es donde
       debe corregirse.
    3. Si la clave no existe NI en español, es un typo del código: se
       lanza KeyError con un mensaje que sugiere revisar la convención.
    """
    catalogo = CATALOGOS.get(idioma, CATALOGOS[IDIOMA_POR_DEFECTO])
    if clave in catalogo:
        return catalogo[clave]
    if clave in CATALOGOS[IDIOMA_POR_DEFECTO]:
        return CATALOGOS[IDIOMA_POR_DEFECTO][clave]
    disponibles = len(CATALOGOS[IDIOMA_POR_DEFECTO])
    raise KeyError(
        f"Clave de i18n inexistente: {clave!r}. Revisar la convencion de "
        f"areas en frontend/i18n/__init__.py y agregarla a los 3 catalogos "
        f"(hoy hay {disponibles} claves)."
    )
