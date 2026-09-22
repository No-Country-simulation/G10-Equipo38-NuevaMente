"""Tests de los catálogos i18n (issue #06).

Verificación exacta del issue: `pytest frontend/tests/test_i18n.py`
(paridad de claves) + revisión de redacción (esa parte es humana: el PR
deja el checklist abierto).

Qué protege cada test:

1. PARIDAD: los 3 catálogos tienen EXACTAMENTE las mismas claves. Si una
   traducción se agrega en un idioma y se olvida en otro, este test
   nombra las claves huérfanas.
2. COBERTURA DE ENUMS: cada valor de máquina de los enums del contrato
   (issue #03) tiene su etiqueta humana en los 3 idiomas. Es el guardián
   de §17.3: "los códigos de API permanecen estables; las etiquetas se
   localizan". Un enum nuevo sin etiqueta explota acá, no en la demo.
3. SIN VACÍOS: ninguna traducción es cadena vacía (un texto que falta es
   un bug de UI, no un espacio en blanco aceptable).
4. FALLBACK de t(): idioma con clave faltante cae al español; clave
   inexistente en todos lados lanza KeyError con mensaje útil.
5. SINTAXIS DE PLACEHOLDERS: las claves con {placeholder} usan los MISMOS
   nombres de placeholder en los 3 idiomas (si en pt fuera {fila} y en
   es {posicion}, el format() reventaría solo en portugués).
"""

import pytest
from i18n import CATALOGOS, t

pytestmark = pytest.mark.unit

IDIOMAS = ("es", "en", "pt")


def test_paridad_de_claves_entre_catalogos():
    """Criterio 1 del issue: un test compara las claves y falla si difieren."""
    claves_es = set(CATALOGOS["es"])
    for idioma in ("en", "pt"):
        claves = set(CATALOGOS[idioma])
        faltan = claves_es - claves
        sobran = claves - claves_es
        assert not faltan, f"{idioma}: claves sin traducir ({len(faltan)}): {sorted(faltan)[:10]}"
        assert not sobran, f"{idioma}: claves que ya no existen en es: {sorted(sobran)[:10]}"


def test_todos_los_enums_del_contrato_tienen_etiqueta_humana():
    """§17.3: valor de máquina estable -> etiqueta traducida por idioma."""
    from app.schemas.enums import (
        DocumentStatus,
        JobStatus,
        OutputLanguage,
    )

    esperadas = {"estados.trabajo." + e.value for e in JobStatus}
    esperadas |= {"estados.documento." + e.value for e in DocumentStatus}
    esperadas |= {"idioma." + e.value for e in OutputLanguage}
    # Perfiles, formatos, nichos y niveles: los tres idiomas deben poder
    # etiquetar los combos del sidebar completo.
    from app.schemas.enums import DetailLevel, IndustryNiche, PedagogicalFormat, RecipientProfile

    esperadas |= {"perfil." + e.value for e in RecipientProfile}
    esperadas |= {"formato." + e.value for e in PedagogicalFormat}
    esperadas |= {"nicho." + e.value for e in IndustryNiche}
    esperadas |= {"detalle." + e.value for e in DetailLevel}

    for idioma in IDIOMAS:
        sin_etiqueta = [clave for clave in sorted(esperadas) if clave not in CATALOGOS[idioma]]
        assert not sin_etiqueta, f"{idioma}: enums sin etiqueta humana: {sin_etiqueta}"


def test_todos_los_codigos_de_error_tienen_mensaje_humano():
    """Cada ErrorCode estable del contrato (7.3) tiene su errors.<code>."""
    from app.schemas.errors import ErrorCode

    for codigo in ErrorCode:
        clave = f"errors.{codigo.value}"
        for idioma in IDIOMAS:
            assert clave in CATALOGOS[idioma], f"{idioma}: falta {clave}"


def test_ninguna_traduccion_vacia():
    for idioma in IDIOMAS:
        vacias = [clave for clave, texto in CATALOGOS[idioma].items() if not texto.strip()]
        assert not vacias, f"{idioma}: traducciones vacías: {vacias}"


def test_t_devuelve_la_traduccion_del_idioma_pedido():
    assert t("comun.guardar", "es") == "Guardar"
    assert t("comun.guardar", "en") == "Save"
    assert t("comun.guardar", "pt") == "Salvar"
    # Idioma por defecto: español (17.1).
    assert t("comun.guardar") == "Guardar"


def test_t_hace_fallback_al_espanol():
    """Clave presente solo en es (simulada): el usuario ve español, no la clave."""
    catalogo_en = CATALOGOS["en"]
    original = catalogo_en.pop("comun.cancelar")
    try:
        # "comun.cancelar" sigue existiendo en es: el fallback debe devolverla.
        assert t("comun.cancelar", "en") == "Cancelar"
    finally:
        # Restaurar SIEMPRE: los catálogos se comparten entre tests.
        catalogo_en["comun.cancelar"] = original


def test_t_explota_con_mensaje_util_para_claves_inexistentes():
    with pytest.raises(KeyError, match="quiz.clave_inventada"):
        t("quiz.clave_inventada", "pt")


def test_placeholders_identicos_entre_idiomas():
    """Las claves con {marcadores} usan los mismos nombres en los 3 idiomas."""
    import re

    for clave, texto_es in CATALOGOS["es"].items():
        marcas_es = set(re.findall(r"\{(\w+)\}", texto_es))
        for idioma in ("en", "pt"):
            marcas = set(re.findall(r"\{(\w+)\}", CATALOGOS[idioma][clave]))
            assert marcas == marcas_es, (
                f"{clave}: placeholders {sorted(marcas)} en {idioma} difieren de {sorted(marcas_es)} en es"
            )
