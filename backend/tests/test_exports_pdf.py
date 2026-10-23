"""#44: los cinco PDFs, Unicode embebido, separación y paginación de contenido extenso."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO

import pytest
import test_exports_markdown as exportaciones
from app.exports.pdf import renderizar_pdf
from app.exports.router import ServicioExportaciones
from app.schemas.responses import PedagogicalOutput
from app.storage.object_keys import clave_export
from app.storage.provider import StorageUnavailable
from pypdf import PdfReader
from test_schemas import FLASHCARDS, GUION, QUIZ, TODOS_LOS_FORMATOS, TUTORIAL, salida_valida

pytestmark = pytest.mark.unit
espacio_exportaciones = exportaciones.espacio_exportaciones


def leer(datos):
    return PdfReader(BytesIO(datos), strict=True)


@pytest.mark.parametrize("tipo", TODOS_LOS_FORMATOS)
@pytest.mark.parametrize(
    "idioma,objetivos",
    [("es", "Objetivos de aprendizaje"), ("en", "Learning objectives"), ("pt", "Objetivos de aprendizagem")],
)
def test_cinco_formatos_tres_idiomas_fuentes_embebidas_y_determinismo(tipo, idioma, objetivos):
    datos = salida_valida(TODOS_LOS_FORMATOS[tipo][0]).model_dump(mode="json")
    datos["metadatos"]["idioma_salida"] = idioma
    datos["contenido_adaptado"]["titulo"] = "Árbol: ação, conexão, São Paulo, informação, ¿por qué?"
    paquete = PedagogicalOutput.model_validate(datos)
    antes = paquete.model_dump_json()
    pdf = renderizar_pdf(paquete)
    lector = leer(pdf)
    texto = "\n".join(p.extract_text() for p in lector.pages)
    assert datos["contenido_adaptado"]["titulo"] in " ".join(texto.split()) and objetivos in texto
    assert "chk_001" in texto and "Conceptos" in texto
    assert "outputs/" not in texto and "modelo_generacion" not in texto
    assert lector.trailer["/Root"]["/Lang"] == idioma
    assert all(float(p.mediabox.width) == pytest.approx(595.2756) for p in lector.pages)
    fuentes = {r.idnum: r.get_object() for p in lector.pages for r in p["/Resources"]["/Font"].values()}
    embebidas = [f for f in fuentes.values() if "/FontDescriptor" in f]
    assert len(embebidas) >= 2
    assert all(f["/FontDescriptor"]["/FontFile2"].get_data() and f["/ToUnicode"] for f in embebidas)
    assert renderizar_pdf(paquete) == pdf and paquete.model_dump_json() == antes


def test_quiz_soluciones_en_paginas_distintas_al_final():
    contenido = deepcopy(QUIZ)
    contenido["preguntas"][0]["justificacion"] = "EXPLICACION_RESERVADA_UNO"
    contenido["preguntas"].append(
        {
            **deepcopy(contenido["preguntas"][0]),
            "id": "q_002",
            "enunciado": "SEGUNDA_PREGUNTA",
            "justificacion": "EXPLICACION_RESERVADA_DOS",
        }
    )
    paginas = [p.extract_text() for p in leer(renderizar_pdf(salida_valida(contenido))).pages]
    indice = next(i for i, t in enumerate(paginas) if "Soluciones" in t)
    preguntas, soluciones = "\n".join(paginas[:indice]), "\n".join(paginas[indice:])
    assert "SEGUNDA_PREGUNTA" in preguntas
    assert "Respuesta correcta" not in preguntas and "EXPLICACION_RESERVADA" not in preguntas
    assert "EXPLICACION_RESERVADA_UNO" in soluciones and "EXPLICACION_RESERVADA_DOS" in soluciones
    assert "Documento fuente" not in soluciones  # Las soluciones son la última sección.


def test_codigo_extenso_y_linea_larga_se_parten_sin_perder_contenido():
    contenido = deepcopy(TUTORIAL)
    codigo = "\n".join(f'    print("ação_{i:03}")' for i in range(130)) + "\n" + "x" * 1000
    contenido["pasos"][0]["codigo"] = codigo
    pdf = leer(renderizar_pdf(salida_valida(contenido)))
    texto = "\n".join(p.extract_text() for p in pdf.pages)
    assert len(pdf.pages) >= 3
    for i in range(130):
        assert f'print("ação_{i:03}")' in texto
    assert texto.count("x") >= 1000
    assert "+" in texto and "Resultado esperado" in texto and "Verificación" in texto
    assert any(
        "JetBrainsMono" in str(f.get_object().get("/BaseFont"))
        for p in pdf.pages
        for f in p["/Resources"]["/Font"].values()
    )


def test_tarjeta_mayor_que_una_pagina_no_se_recorta():
    contenido = deepcopy(FLASHCARDS)
    contenido["items"][0]["frente"] = "INICIO " + "VCN y conexión. " * 300 + " FIN_FRENTE"
    contenido["items"][0]["dorso"] = "INICIO " + "ação y aislamiento. " * 220 + " FIN_DORSO"
    paginas = [p.extract_text() for p in leer(renderizar_pdf(salida_valida(contenido))).pages]
    texto = "\n".join(paginas)
    assert len(paginas) >= 3 and "FIN_FRENTE" in texto and "FIN_DORSO" in texto
    assert texto.count("Frente") > 1 and texto.count("Dorso") > 1


def test_xml_imagenes_links_y_codigo_de_la_fuente_se_imprimen_como_texto():
    contenido = deepcopy(GUION)
    malicioso = '<img src="https://privado.invalid/pixel"/><link href="file:///secreto">clique</link><script>x</script>'
    contenido["escenas"][0]["narracion"] = malicioso
    lector = leer(renderizar_pdf(salida_valida(contenido)))
    texto = " ".join(" ".join(p.extract_text() for p in lector.pages).split())
    assert malicioso in texto
    assert "/OpenAction" not in lector.trailer["/Root"] and "/AA" not in lector.trailer["/Root"]
    for pagina in lector.pages:
        assert "/XObject" not in pagina["/Resources"]
        for anotacion in pagina.get("/Annots", []):
            anotacion = anotacion.get_object()
            assert "/A" not in anotacion or anotacion["/A"].get("/S") != "/URI"


def test_cache_pdf_concurrencia_reinicio_e_integridad(espacio_exportaciones):
    e = espacio_exportaciones
    servicio = ServicioExportaciones(e.catalogo)
    with ThreadPoolExecutor(2) as pool:
        a, b = [pool.submit(servicio.descargar, e.token, e.paquete.generation_id, "pdf") for _ in range(2)]
        assert a.result(5) == b.result(5)
    assert ServicioExportaciones(e.catalogo).descargar(e.token, e.paquete.generation_id, "pdf") == a.result()
    e.storage.upload(clave_export("ws_estudio", e.paquete.generation_id, "pdf"), b"ajeno")
    with pytest.raises(StorageUnavailable):
        servicio.descargar(e.token, e.paquete.generation_id, "pdf")


def test_renderizar_pdf_multidocumento(espacio_exportaciones):
    e = espacio_exportaciones
    import io

    from app.exports.pdf import renderizar_pdf
    from app.schemas.pedagogical import Referencia
    from app.schemas.responses import DocumentoFuente
    from pypdf import PdfReader

    p = e.paquete.model_copy(deep=True)
    f1 = DocumentoFuente(document_id="doc1", titulo="Doc 1", version="v1", hash="hash1", procedencia="p1")
    f2 = DocumentoFuente(document_id="doc2", titulo="Doc 2", version="v2", hash="hash2", procedencia="p2")
    p.documento_fuente = f1
    p.documentos_fuente = [f1, f2]
    refs = [
        Referencia(document_id="doc1", chunk_id="chk1", pagina=1, seccion="Sec A"),
        Referencia(document_id="doc2", chunk_id="chk2", pagina=2, seccion="Sec B"),
    ]
    if hasattr(p.contenido_adaptado, "items"):
        p.contenido_adaptado.items[0].referencias = refs
    elif hasattr(p.contenido_adaptado, "preguntas"):
        p.contenido_adaptado.preguntas[0].referencias = refs
    salida = renderizar_pdf(p)
    lector = PdfReader(io.BytesIO(salida))
    texto_total = "".join(pag.extract_text() for pag in lector.pages)
    assert "Doc 1" in texto_total and "Doc 2" in texto_total
    assert "doc1" in texto_total
    assert "doc2" in texto_total
