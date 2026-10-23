"""Markdown didáctico desde tipos canónicos; no ejecuta código ni usa LLM."""

import html
import re

from app.exports.generation_mode import etiqueta_generacion
from app.schemas.pedagogical import (
    ExecutiveSummary,
    FlashcardDeck,
    InteractiveQuiz,
    PracticalTutorial,
    VideoLessonScript,
)
from app.schemas.responses import PedagogicalOutput

_ETIQUETAS = {
    "es": {
        "audiencia": "Audiencia",
        "idioma": "Idioma",
        "tiempo": "Tiempo estimado de estudio",
        "minutos": "minutos",
        "objetivos": "Objetivos de aprendizaje",
        "prerrequisitos": "Prerrequisitos",
        "ninguno": "Sin prerrequisitos declarados.",
        "conceptos": "Conceptos clave",
        "introduccion": "Introducción",
        "tarjetas": "Tarjetas",
        "tarjeta": "Tarjeta",
        "frente": "Frente",
        "dorso": "Dorso",
        "pista": "Pista didáctica",
        "etiquetas": "Etiquetas",
        "preguntas": "Preguntas",
        "pregunta": "Pregunta",
        "soluciones": "Soluciones",
        "correcta": "Respuesta correcta",
        "justificacion": "Justificación",
        "pasos": "Pasos",
        "paso": "Paso",
        "resultado": "Resultado esperado",
        "verificacion": "Verificación",
        "codigo": "Código",
        "puntos": "Puntos clave",
        "impacto": "Impacto cualitativo",
        "implicaciones": "Implicaciones",
        "acciones": "Acciones",
        "objetivos_clase": "Objetivos de la clase",
        "escenas": "Escenas",
        "escena": "Escena",
        "narracion": "Narración",
        "diapositiva": "Diapositiva",
        "interaccion": "Pregunta interactiva",
        "fuente": "Documento fuente",
        "fuentes": "Documentos fuente",
        "documento": "Documento",
        "version": "Versión",
        "procedencia": "Procedencia",
        "referencias": "Referencias",
        "pagina": "Página",
        "seccion": "Sección",
        "fragmento": "Fragmento",
        "principiante": "Principiante / Transición de carrera",
        "junior_ssr": "Desarrollador Junior / Semi-Senior",
        "lider_tecnico": "Líder técnico / Arquitecto",
        "ejecutivo": "Gestor / Ejecutivo",
    },
    "en": {
        "audiencia": "Audience",
        "idioma": "Language",
        "tiempo": "Estimated study time",
        "minutos": "minutes",
        "objetivos": "Learning objectives",
        "prerrequisitos": "Prerequisites",
        "ninguno": "No prerequisites declared.",
        "conceptos": "Key concepts",
        "introduccion": "Introduction",
        "tarjetas": "Cards",
        "tarjeta": "Card",
        "frente": "Front",
        "dorso": "Back",
        "pista": "Learning hint",
        "etiquetas": "Tags",
        "preguntas": "Questions",
        "pregunta": "Question",
        "soluciones": "Solutions",
        "correcta": "Correct answer",
        "justificacion": "Explanation",
        "pasos": "Steps",
        "paso": "Step",
        "resultado": "Expected result",
        "verificacion": "Verification",
        "codigo": "Code",
        "puntos": "Key points",
        "impacto": "Qualitative impact",
        "implicaciones": "Implications",
        "acciones": "Actions",
        "objetivos_clase": "Lesson objectives",
        "escenas": "Scenes",
        "escena": "Scene",
        "narracion": "Narration",
        "diapositiva": "Slide",
        "interaccion": "Interactive question",
        "fuente": "Source document",
        "fuentes": "Source documents",
        "documento": "Document",
        "version": "Version",
        "procedencia": "Provenance",
        "referencias": "References",
        "pagina": "Page",
        "seccion": "Section",
        "fragmento": "Chunk",
        "principiante": "Beginner / Career transition",
        "junior_ssr": "Junior / Mid-level developer",
        "lider_tecnico": "Technical lead / Architect",
        "ejecutivo": "Manager / Executive",
    },
    "pt": {
        "audiencia": "Público",
        "idioma": "Idioma",
        "tiempo": "Tempo estimado de estudo",
        "minutos": "minutos",
        "objetivos": "Objetivos de aprendizagem",
        "prerrequisitos": "Pré-requisitos",
        "ninguno": "Sem pré-requisitos declarados.",
        "conceptos": "Conceitos-chave",
        "introduccion": "Introdução",
        "tarjetas": "Cartões",
        "tarjeta": "Cartão",
        "frente": "Frente",
        "dorso": "Verso",
        "pista": "Dica didática",
        "etiquetas": "Etiquetas",
        "preguntas": "Perguntas",
        "pregunta": "Pergunta",
        "soluciones": "Soluções",
        "correcta": "Resposta correta",
        "justificacion": "Justificativa",
        "pasos": "Passos",
        "paso": "Passo",
        "resultado": "Resultado esperado",
        "verificacion": "Verificação",
        "codigo": "Código",
        "puntos": "Pontos-chave",
        "impacto": "Impacto qualitativo",
        "implicaciones": "Implicações",
        "acciones": "Ações",
        "objetivos_clase": "Objetivos da aula",
        "escenas": "Cenas",
        "escena": "Cena",
        "narracion": "Narração",
        "diapositiva": "Slide",
        "interaccion": "Pergunta interativa",
        "fuente": "Documento-fonte",
        "fuentes": "Documentos-fonte",
        "documento": "Documento",
        "version": "Versão",
        "procedencia": "Procedência",
        "referencias": "Referências",
        "pagina": "Página",
        "seccion": "Seção",
        "fragmento": "Fragmento",
        "principiante": "Iniciante / Transição de carreira",
        "junior_ssr": "Desenvolvedor Júnior / Pleno",
        "lider_tecnico": "Líder técnico / Arquiteto",
        "ejecutivo": "Gestor / Executivo",
    },
}
_IDIOMAS = {"es": "Español", "en": "English", "pt": "Português"}


# con esta funcion sanitizo y escapo caracteres especiales para generar texto markdown seguro sin inyecciones accidentales de etiquetas o formato
def texto(valor: str, *, una_linea=False) -> str:
    """Texto del documento como texto: sin HTML activo, enlaces o títulos inyectados."""
    limpio = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", valor.replace("\r\n", "\n").replace("\r", "\n"))
    if una_linea:
        limpio = " ".join(limpio.split())
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(limpio, quote=False))


# con esta funcion calculo la cantidad necesaria de acentos graves (backticks) para aislar bloques de codigo sin romper la delimitacion de markdown
def bloque_codigo(codigo: str) -> str:
    # Un cierre de fence incluido en el snippet nunca sale del bloque literal.
    longitud = max((len(m.group()) for m in re.finditer(r"`+", codigo)), default=0)
    fence = "`" * max(3, longitud + 1)
    return fence + "\n" + codigo.replace("\r\n", "\n").replace("\r", "\n") + "\n" + fence


# con esta clase auxiliar compilo el contenido educativo en formato markdown estructurando titulos listas tablas y notas al pie para las citas
class _Plantilla:
    def __init__(self, paquete):
        self.l = _ETIQUETAS[paquete.metadatos.idioma_salida.value]
        self.partes = []
        self.referencias = {}
        for r in paquete.referencias:
            self._referencia(r)

    def _referencia(self, r):
        doc_id = getattr(r, "document_id", None)
        clave = (doc_id, r.chunk_id, r.pagina, r.seccion)
        if clave not in self.referencias:
            self.referencias[clave] = len(self.referencias) + 1
        return self.referencias[clave]

    def agregar(self, valor):
        self.partes.append(valor)

    def titulo(self, clave, nivel=2, sufijo=""):
        self.agregar("#" * nivel + " " + self.l[clave] + sufijo)

    def campo(self, clave, valor):
        self.agregar("**" + self.l[clave] + ":** " + texto(valor))

    def lista(self, clave, valores):
        self.titulo(clave)
        self.agregar("\n".join("- " + texto(v).replace("\n", "\n  ") for v in valores) or self.l["ninguno"])

    def citas(self, referencias):
        self.agregar(" ".join(f"[^{n}]" for n in dict.fromkeys(self._referencia(r) for r in referencias)))

    def terminar(self):
        self.titulo("referencias")
        lineas = []
        for (doc_id, cid, pagina, seccion), n in self.referencias.items():
            linea = f"[^{n}]: "
            if doc_id:
                linea += f"{self.l.get('documento', 'Documento')}: {texto(doc_id, una_linea=True)} · "
            linea += f"{self.l['fragmento']}: {texto(cid, una_linea=True)}"
            if pagina is not None:
                linea += f" · {self.l['pagina']}: {pagina}"
            if seccion is not None:
                linea += f" · {self.l['seccion']}: {texto(seccion, una_linea=True)}"
            lineas.append(linea)
        self.agregar("\n".join(lineas))
        return "\n\n".join(self.partes) + "\n"


# aqui transformo el paquete educativo aprobado en un documento markdown enriquecido con codificacion utf-8 segun el issue 23
def renderizar_markdown(paquete: PedagogicalOutput) -> bytes:
    p, contenido, m = _Plantilla(paquete), paquete.contenido_adaptado, paquete.metadatos
    p.agregar("# " + texto(contenido.titulo, una_linea=True))
    if etiqueta := etiqueta_generacion(paquete):
        p.agregar("> " + texto(etiqueta))
    p.campo("audiencia", p.l[m.perfil_aplicado.value])
    p.campo("idioma", _IDIOMAS[m.idioma_salida.value])
    p.campo("tiempo", f"{m.tiempo_estimado_estudio_minutos} {p.l['minutos']}")
    p.lista("objetivos", m.objetivos_aprendizaje)
    prerrequisitos = list(
        dict.fromkeys(m.prerrequisitos + (contenido.prerrequisitos if isinstance(contenido, PracticalTutorial) else []))
    )
    p.lista("prerrequisitos", prerrequisitos)
    p.lista("conceptos", m.conceptos_clave)
    introduccion = getattr(contenido, "introduccion_contextualizada", None)
    if introduccion:
        p.titulo("introduccion")
        p.agregar(texto(introduccion))
    if isinstance(contenido, FlashcardDeck):
        p.titulo("tarjetas")
        for i, tarjeta in enumerate(contenido.items, 1):
            p.titulo("tarjeta", 3, f" {i}")
            p.campo("frente", tarjeta.frente)
            p.campo("dorso", tarjeta.dorso)
            if tarjeta.pista_didactica:
                p.campo("pista", tarjeta.pista_didactica)
            if tarjeta.etiquetas:
                p.campo("etiquetas", ", ".join(tarjeta.etiquetas))
            p.citas(tarjeta.referencias)
    elif isinstance(contenido, InteractiveQuiz):
        p.titulo("preguntas")
        for i, pregunta in enumerate(contenido.preguntas, 1):
            p.titulo("pregunta", 3, f" {i}")
            p.agregar(texto(pregunta.enunciado))
            p.agregar(
                "\n".join(
                    f"- **{texto(o.option_id, una_linea=True)}:** {texto(o.texto).replace(chr(10), chr(10) + '  ')}"
                    for o in pregunta.opciones
                )
            )
        p.titulo("soluciones")
        for i, pregunta in enumerate(contenido.preguntas, 1):
            p.titulo("pregunta", 3, f" {i}")
            opcion = next(o for o in pregunta.opciones if o.option_id == pregunta.correct_option_id)
            p.campo("correcta", opcion.option_id + ": " + opcion.texto)
            p.campo("justificacion", pregunta.justificacion)
            p.citas(pregunta.referencias)
    elif isinstance(contenido, PracticalTutorial):
        p.campo("audiencia", contenido.audiencia)
        p.titulo("pasos")
        for i, paso in enumerate(contenido.pasos, 1):
            p.titulo("paso", 3, f" {i}")
            p.agregar(texto(paso.instruccion))
            if paso.codigo:
                p.titulo("codigo", 4)
                p.agregar(bloque_codigo(paso.codigo))
            p.campo("resultado", paso.resultado_esperado)
            p.campo("verificacion", paso.verificacion)
            p.citas(paso.referencias)
    elif isinstance(contenido, ExecutiveSummary):
        p.lista("puntos", contenido.puntos_clave)
        p.titulo("impacto")
        p.agregar(texto(contenido.impacto_cualitativo))
        p.lista("implicaciones", contenido.implicaciones)
        p.lista("acciones", contenido.acciones)
        p.citas(contenido.referencias)
    elif isinstance(contenido, VideoLessonScript):
        p.lista("objetivos_clase", contenido.objetivos)
        p.titulo("escenas")
        for i, escena in enumerate(contenido.escenas, 1):
            p.titulo("escena", 3, f" {i} ({escena.duracion_min:g} {p.l['minutos']})")
            p.campo("narracion", escena.narracion)
            p.agregar(
                "**"
                + p.l["diapositiva"]
                + ":**\n\n"
                + "\n".join("- " + texto(v).replace("\n", "\n  ") for v in escena.puntos_diapositiva)
            )
            if escena.pregunta_interactiva:
                p.campo("interaccion", escena.pregunta_interactiva)
            p.citas(escena.referencias)
    else:
        raise TypeError("Formato pedagógico no soportado")
    fuentes = getattr(paquete, "documentos_fuente", None) or [paquete.documento_fuente]
    if len(fuentes) > 1:
        p.titulo("fuentes")
        for f in fuentes:
            p.agregar("### " + texto(f.titulo))
            p.campo("version", f.version)
            p.agregar("SHA-256: " + texto(f.hash, una_linea=True))
            if f.procedencia:
                p.campo("procedencia", f.procedencia)
    else:
        fuente = fuentes[0]
        p.titulo("fuente")
        p.agregar(texto(fuente.titulo))
        p.campo("version", fuente.version)
        p.agregar("SHA-256: " + texto(fuente.hash, una_linea=True))
        if fuente.procedencia:
            p.campo("procedencia", fuente.procedencia)
    return p.terminar().encode("utf-8")
