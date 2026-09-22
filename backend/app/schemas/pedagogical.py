"""Los 5 formatos pedagógicos tipados (issue #03, referencia §16.2).

Este archivo define el CORAZÓN del contrato: los modelos de
``contenido_adaptado``, la parte del paquete que cambia según el formato
elegido por el usuario (tutorial, flashcards, quiz, resumen ejecutivo o
guion de clase).

Cómo se lee esto si no conocés Pydantic:

- Un ``BaseModel`` describe un objeto JSON: cada atributo de clase es una
  clave del JSON con su tipo y sus restricciones. ``titulo: str`` dice que
  la clave "titulo" debe ser string; ``Field(min_length=1)`` agrega que no
  puede estar vacío.
- Pydantic VALIDA al construir el modelo: si el JSON no cumple, lanza
  ``ValidationError`` indicando campo y motivo (criterio de aceptación del
  issue: "un payload inválido es rechazado con error de Pydantic claro").
- ``model_config = ConfigDict(extra="forbid")`` rechaza claves desconocidas
  (§7.2): el contrato no crece en silencio.
- ``model_validator(mode="after")`` son validaciones SEMÁNTICAS que miran
  varios campos a la vez (por ejemplo: la respuesta correcta de una
  pregunta debe ser una de sus opciones).

La unión discriminada (§16.2 "Cada formato incorpora tipo con un Literal
propio para discriminar la unión"):

    ContenidoAdaptado = FlashcardDeck | InteractiveQuiz | ... | VideoLessonScript

Todos los modelos tienen un campo ``tipo`` con un valor ÚNICO ("flashcards",
"quiz", ...). Al parsear un JSON, Pydantic mira esa clave y elige el modelo
correcto — es un ``switch`` automatizado que evita intentar parsear cada
formato a ver cuál calza.

Nombres en español: las CLAVES del JSON público son el contrato y van en
español (§16.3); los nombres de clase pueden ir en inglés sin crear un
segundo contrato de salida.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# -----------------------------------------------------------------------------
# Piezas compartidas por varios formatos
# -----------------------------------------------------------------------------


class ModeloContenido(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def textos_no_vacios(cls, valor):
        elementos = valor if isinstance(valor, list) else [valor]
        if any(isinstance(item, str) and not item.strip() for item in elementos):
            raise ValueError("El texto no puede estar vacío ni contener solo espacios")
        return valor


class Referencia(ModeloContenido):
    """Dónde verificar una afirmación dentro del documento original.

    Toda tarjeta, pregunta, paso o escena cita su evidencia: un ``chunk_id``
    (identificador estable del fragmento, issue #12) y opcionalmente su
    página y sección. La UI usa esto para el botón «Ver la fuente»
    (GET /api/documents/{id}/sources/{chunk_id}).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, description="Fragmento de origen (issue #12).")
    pagina: int | None = Field(default=None, ge=1, description="Página del documento original.")
    seccion: str | None = Field(default=None, description="Sección/título del documento original.")


class AlcanceSolicitud(ModeloContenido):
    """Qué parte del documento se adapta (§16.1).

    Se usa DOS veces con el mismo modelo: en la petición de generación
    (``{"tipo": "documento_completo"}`` o ``{"tipo": "seccion",
    "seccion_id": "sec_3"}``). La subclase de salida ``Alcance``, dentro de
    ``metadatos``, agrega ``secciones_cubiertas`` para declarar la cobertura
    real lograda (contratos-api.md: "alcance conserva la estructura de
    entrada y agrega secciones cubiertas; no alterna objeto/string").
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["documento_completo", "seccion"] = Field(
        default="documento_completo",
        description="documento_completo es el valor por defecto; seccion exige seccion_id.",
    )
    seccion_id: str | None = Field(
        default=None,
        description="Obligatorio cuando tipo=seccion; ignorado en documento_completo.",
    )

    @model_validator(mode="after")
    def _seccion_exige_seccion_id(self) -> "AlcanceSolicitud":
        """Validación semántica: elegir 'seccion' sin decir cuál es un error."""
        if self.tipo == "seccion" and (not self.seccion_id or not self.seccion_id.strip()):
            raise ValueError("alcance.tipo='seccion' exige seccion_id no vacío")
        return self


class Alcance(AlcanceSolicitud):
    """Alcance de salida con cobertura calculada por el backend."""

    secciones_cubiertas: list[str] | None = Field(
        default=None,
        description="Solo en la respuesta: secciones efectivamente cubiertas por la adaptación.",
    )


# Calibración inicial de límites de longitud (§16.2 pide "límites de
# longitud" sin fijar cifras): generosos para contenido educativo real,
# suficientes para rechazar payloads basura o truncados.
TITULO_MAX = 300
TEXTO_MAX = 5000


# -----------------------------------------------------------------------------
# 1. Flashcards
# -----------------------------------------------------------------------------


class FlashcardItem(ModeloContenido):
    """Una tarjeta de memorización: frente (pregunta), dorso (respuesta)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, description="Identificador único dentro del mazo.")
    frente: str = Field(min_length=1, max_length=TEXTO_MAX, description=" Cara de la tarjeta: pregunta o concepto.")
    dorso: str = Field(min_length=1, max_length=TEXTO_MAX, description="Reverso: la respuesta a memorizar.")
    pista_didactica: str | None = Field(
        default=None, max_length=TEXTO_MAX, description="Ayuda opcional para evocar el dorso."
    )
    etiquetas: list[str] = Field(
        default_factory=list, max_length=20, description="Etiquetas temáticas libres (p. ej. 'redes')."
    )
    referencias: list[Referencia] = Field(
        min_length=1, description="Evidencia que respalda el dorso; sin respaldo no se aprueba."
    )


class FlashcardDeck(ModeloContenido):
    """Mazo de flashcards (formato_salida=flashcards).

    Es el formato que se exporta a Anki (.apkg/CSV/TSV) con el issue #45;
    la estructura de cada item es lo que esas exportaciones consumen.
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["flashcards"] = Field(description="Discriminador de la unión contenido_adaptado.")
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    introduccion_contextualizada: str | None = Field(
        default=None, max_length=TEXTO_MAX, description="Contexto inicial adaptado al perfil."
    )
    items: list[FlashcardItem] = Field(min_length=1, description="Listas vacías se rechazan (§16.2).")

    @model_validator(mode="after")
    def _ids_unicos(self) -> "FlashcardDeck":
        """§16.2 exige IDs únicos: duplicados romperían el seguimiento de repaso."""
        duplicados = {x.id for x in self.items if [y.id for y in self.items].count(x.id) > 1}
        if duplicados:
            raise ValueError(f"IDs de flashcard duplicados: {sorted(duplicados)}")
        return self


# -----------------------------------------------------------------------------
# 2. Quiz interactivo
# -----------------------------------------------------------------------------


class QuizOption(ModeloContenido):
    """Una opción de respuesta multiple choice."""

    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=8, description="Letra/etiqueta de la opción (p. ej. 'A').")
    texto: str = Field(min_length=1, max_length=TEXTO_MAX)


class QuizQuestion(ModeloContenido):
    """Una pregunta del quiz, con su clave y justificación didáctica.

    OJO (§16.2 y contratos-api.md): la respuesta correcta y la justificación
    son SECRETAS para la vista de estudiante. Este modelo es la vista
    CANÓNICA (completa); la vista sin clave se arma con
    ``InteractiveQuiz.vista_estudiante()``. La corrección en tiempo real
    (issue #35) compara contra este contenido aprobado SIN gastar otra
    llamada LLM (§7.5).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, description="Identificador único dentro del quiz.")
    enunciado: str = Field(min_length=1, max_length=TEXTO_MAX)
    opciones: list[QuizOption] = Field(
        min_length=4,
        max_length=4,
        description="§16.2 fija exactamente cuatro opciones con IDs distintos.",
    )
    correct_option_id: str = Field(description="Debe ser el option_id de una de sus propias opciones.")
    justificacion: str = Field(
        min_length=1,
        max_length=TEXTO_MAX,
        description="Por qué es correcta y por qué las demás no (feedback inmediato).",
    )
    referencias: list[Referencia] = Field(min_length=1)

    @model_validator(mode="after")
    def _validar_opciones(self) -> "QuizQuestion":
        """Tres reglas semánticas de §16.2 en un solo lugar:

        1. Los option_id son distintos entre sí (IDs únicos).
        2. correct_option_id pertenece a opciones (la clave no puede apuntar
           a una opción inexistente).
        3. (Estructuralmente garantizado por 1 y 2) hay exactamente una
           respuesta defendible; que la clave sea la "verdadera" y los
           distractores "plausibles pero falsos" lo evalúa el Critic, no
           un schema.
        """
        ids = [o.option_id for o in self.opciones]
        if len(set(ids)) != len(ids):
            raise ValueError(f"option_id duplicados en la pregunta {self.id}: {ids}")
        if self.correct_option_id not in ids:
            raise ValueError(
                f"correct_option_id='{self.correct_option_id}' no pertenece a las opciones "
                f"{ids} de la pregunta {self.id}"
            )
        return self


class InteractiveQuiz(ModeloContenido):
    """Quiz interactivo con feedback inmediato (formato_salida=quiz)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["quiz"] = Field(description="Discriminador de la unión contenido_adaptado.")
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    introduccion_contextualizada: str | None = Field(default=None, max_length=TEXTO_MAX)
    preguntas: list[QuizQuestion] = Field(min_length=1, description="Listas vacías se rechazan (§16.2).")

    @model_validator(mode="after")
    def _ids_unicos(self) -> "InteractiveQuiz":
        duplicados = {x.id for x in self.preguntas if [y.id for y in self.preguntas].count(x.id) > 1}
        if duplicados:
            raise ValueError(f"IDs de pregunta duplicados: {sorted(duplicados)}")
        return self

    def vista_estudiante(self) -> "QuizVistaEstudiante":
        """Vista SIN clave ni justificaciones (§16.2, "La vista inicial del
        estudiante omite la clave y las justificaciones hasta responder").

        El endpoint de respuestas (issue #35) devuelve la corrección puntual;
        la clave completa SOLO viaja en el paquete exportado (PDF con sección
        de soluciones separada, issue #44).
        """
        return QuizVistaEstudiante(
            titulo=self.titulo,
            introduccion_contextualizada=self.introduccion_contextualizada,
            preguntas=[
                PreguntaVistaEstudiante(id=p.id, enunciado=p.enunciado, opciones=list(p.opciones))
                for p in self.preguntas
            ],
        )


class PreguntaVistaEstudiante(ModeloContenido):
    """Pregunta de quiz tal como la recibe el estudiante: SIN respuesta ni justificación."""

    model_config = ConfigDict(extra="forbid")

    id: str
    enunciado: str
    opciones: list[QuizOption]


class QuizVistaEstudiante(ModeloContenido):
    tipo: Literal["quiz"] = "quiz"
    """Quiz completo en su vista de estudiante (sin claves)."""

    model_config = ConfigDict(extra="forbid")

    titulo: str
    introduccion_contextualizada: str | None = None
    preguntas: list[PreguntaVistaEstudiante]


# -----------------------------------------------------------------------------
# 3. Tutorial práctico paso a paso
# -----------------------------------------------------------------------------


class PasoTutorial(ModeloContenido):
    """Un paso del tutorial: hacer, observar y verificar (pedagogía del check)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, description="Identificador único del paso.")
    instruccion: str = Field(min_length=1, max_length=TEXTO_MAX, description="Qué hacer en este paso.")
    resultado_esperado: str = Field(
        min_length=1, max_length=TEXTO_MAX, description="Qué debería observarse si salió bien."
    )
    verificacion: str = Field(
        min_length=1, max_length=TEXTO_MAX, description="Cómo comprobar el resultado (check de entendimiento)."
    )
    codigo: str | None = Field(
        default=None, max_length=TEXTO_MAX, description="Snippet opcional; se muestra, no se ejecuta (§11.4)."
    )
    referencias: list[Referencia] = Field(min_length=1)


class PracticalTutorial(ModeloContenido):
    """Guía práctica paso a paso (formato_salida=tutorial)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["tutorial"] = Field(description="Discriminador de la unión contenido_adaptado.")
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    audiencia: str = Field(
        min_length=1, max_length=TITULO_MAX, description="A quién está dirigido (refuerza el perfil aplicado)."
    )
    prerrequisitos: list[str] = Field(
        default_factory=list, description="Conocimientos previos; lista vacía si no aplica (§16.3)."
    )
    pasos: list[PasoTutorial] = Field(min_length=1, description="Listas vacías se rechazan (§16.2).")

    @model_validator(mode="after")
    def _ids_unicos(self) -> "PracticalTutorial":
        duplicados = {x.id for x in self.pasos if [y.id for y in self.pasos].count(x.id) > 1}
        if duplicados:
            raise ValueError(f"IDs de paso duplicados: {sorted(duplicados)}")
        return self


# -----------------------------------------------------------------------------
# 4. Resumen ejecutivo
# -----------------------------------------------------------------------------


class ExecutiveSummary(ModeloContenido):
    """Resumen ejecutivo TL;DR (formato_salida=resumen_ejecutivo).

    Pensado para Gestor/Ejecutivo: valor de negocio e implicaciones antes
    que detalle técnico (§16.1: el perfil fija el vocabulario).
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["resumen_ejecutivo"] = Field(description="Discriminador de la unión contenido_adaptado.")
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    puntos_clave: list[str] = Field(min_length=1, description="Hallazgos centrales; lista vacía se rechaza (§16.2).")
    impacto_cualitativo: str = Field(
        min_length=1, max_length=TEXTO_MAX, description="Impacto sustentado en la fuente, sin métricas inventadas."
    )
    implicaciones: list[str] = Field(min_length=1, description="Qué se sigue de los puntos clave.")
    acciones: list[str] = Field(min_length=1, description="Próximos pasos accionables.")
    referencias: list[Referencia] = Field(min_length=1)


# -----------------------------------------------------------------------------
# 5. Guion de clase / video
# -----------------------------------------------------------------------------


class EscenaGuion(ModeloContenido):
    """Una escena del guion: lo que se narra y lo que se muestra en pantalla."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, description="Identificador único de la escena.")
    duracion_min: float = Field(gt=0, description="§16.2: duraciones positivas.")
    narracion: str = Field(min_length=1, max_length=TEXTO_MAX, description="Texto que se lee/habla.")
    puntos_diapositiva: list[str] = Field(
        min_length=1, description="Bullets de la diapositiva que acompaña la narración."
    )
    pregunta_interactiva: str | None = Field(
        default=None, max_length=TEXTO_MAX, description="Check de comprensión al aire (opcional)."
    )
    referencias: list[Referencia] = Field(min_length=1)


class VideoLessonScript(ModeloContenido):
    """Guion de clase/video (formato_salida=guion_clase).

    §16.2 aclara: es un DOCUMENTO de clase; no se promete generar un archivo
    audiovisual.
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["guion_clase"] = Field(description="Discriminador de la unión contenido_adaptado.")
    titulo: str = Field(min_length=1, max_length=TITULO_MAX)
    objetivos: list[str] = Field(min_length=1, description="Objetivos de la clase; lista vacía se rechaza.")
    escenas: list[EscenaGuion] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_unicos(self) -> "VideoLessonScript":
        duplicados = {x.id for x in self.escenas if [y.id for y in self.escenas].count(x.id) > 1}
        if duplicados:
            raise ValueError(f"IDs de escena duplicados: {sorted(duplicados)}")
        return self


# -----------------------------------------------------------------------------
# La unión discriminada: el campo contenido_adaptado del paquete
# -----------------------------------------------------------------------------

# Annotated añade METADATOS al tipo de la unión; Field(discriminator="tipo")
# le dice a Pydantic qué campo usar para elegir el modelo. El orden de la
# unión no importa: decide siempre el valor de "tipo" en el JSON.
ContenidoAdaptado = Annotated[
    Union[
        FlashcardDeck,
        InteractiveQuiz,
        PracticalTutorial,
        ExecutiveSummary,
        VideoLessonScript,
    ],
    Field(discriminator="tipo"),
]


ContenidoEstudiante = Annotated[
    Union[FlashcardDeck, QuizVistaEstudiante, PracticalTutorial, ExecutiveSummary, VideoLessonScript],
    Field(discriminator="tipo"),
]
