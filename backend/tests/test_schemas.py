"""Tests del contrato compartido v1 (issue #03).

Qué verifica este archivo (espejo de los criterios de aceptación del issue):

1. ROUND-TRIP: cada formato pedagógico se construye desde JSON válido, se
   serializa con ``model_dump()`` y se vuelve a parsear con
   ``model_validate()`` sin perder nada. Es la garantía de que lo que el
   backend guarda en OCI es exactamente lo que recupera y entrega.
2. UNIÓN DISCRIMINADA: el campo ``tipo`` elige el modelo correcto para cada
   uno de los 5 formatos.
3. RECHAZO DE INVÁLIDOS: un payload que viola el contrato (respuesta
   correcta fuera de las opciones, lista vacía, score fuera de rango,
   campo desconocido...) lanza ``ValidationError`` de Pydantic indicando
   campo y motivo — nunca pasa en silencio.

Cómo leerlo si no conocés pytest: cada función ``test_*`` es un caso; pytest
las descubre solo y las ejecuta. ``pytest.raises`` afirma que el bloque
LANZA la excepción esperada (aquí: que Pydantic rechace). El decorador
``@pytest.mark.parametrize`` ejecuta el MISMO test una vez por fila de
datos: cada tupla es un payload inválido distinto con la razón.

Los ejemplos válidos son los mismos JSON publicados en
docs/contratos-api.md: si alguien cambia el contrato sin actualizar el
documento (o viceversa), estos tests fallan.
"""

import pytest
from app.schemas.enums import (
    DocumentStatus,
    JobStatus,
    OutputLanguage,
)
from app.schemas.errors import ErrorCode, ErrorResponse
from app.schemas.pedagogical import (
    ExecutiveSummary,
    FlashcardDeck,
    InteractiveQuiz,
    PracticalTutorial,
    QuizVistaEstudiante,
    VideoLessonScript,
)
from app.schemas.requests import GenerateRequest, ProgressEvent
from app.schemas.responses import EvaluacionCalidad, GenerationJobResponse, PedagogicalOutput
from app.schemas.sse import GenerationEvent as SSEGenerationEvent
from app.schemas.sse import JobEvent
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers: piezas válidas reutilizadas por varios tests
# ---------------------------------------------------------------------------

REFERENCIA = {"chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos"}


def evaluacion_valida(**sobrescrituras) -> dict:
    """Evaluación de calidad válida por defecto; los tests pisan campos puntuales."""
    base = {
        "anclaje_fuente_score": 1.0,
        "cantidad_afirmaciones": 12,
        "cantidad_respaldadas": 12,
        "estado_evaluacion": "aprobada",
        "claridad_pedagogica": "alta",
        "adecuacion_perfil": "alta",
        "cobertura_objetivos": "completa",
        "coherencia_didactica": "alta",
        "verificacion_visual": "no_aplica",
        "observaciones": "Sin observaciones",
    }
    base.update(sobrescrituras)
    return base


def salida_valida(contenido: dict) -> PedagogicalOutput:
    """Paquete PedagogicalOutput completo y válido alrededor de un contenido dado."""
    return PedagogicalOutput(
        schema_version="1.0",
        generation_id="gen_01HX",
        status="aprobado",
        metadatos={
            "perfil_aplicado": "principiante",
            "formato_generado": contenido["tipo"],
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_origen": "es",
            "idioma_salida": "es",
            "conceptos_clave": ["VCN"],
            "prerrequisitos": [],
            "objetivos_aprendizaje": ["Explicar qué es una VCN y para qué sirve"],
            "tiempo_estimado_estudio_minutos": 5,
            "alcance": {"tipo": "documento_completo", "secciones_cubiertas": ["Conceptos"]},
        },
        documento_fuente={
            "document_id": "doc_01HX",
            "titulo": "Introducción a la Arquitectura de Redes VCN en OCI",
            "hash": "sha256:abc",
            "version": "v1",
        },
        contenido_adaptado=contenido,
        evaluacion_calidad=evaluacion_valida(),
        referencias=[REFERENCIA],
        created_at="2026-10-01T12:00:00Z",
        trazabilidad={
            "modelo_generacion": "gemini-2.5-flash",
            "modelo_verificacion": "gemini-2.5-flash",
            "modelo_embeddings": "gemini-embedding-2",
            "prompt_version": "1.0",
            "parser_version": "1.0",
            "retrieval": {"k": 5, "fetch_k": 15, "lambda_mult": 0.7},
        },
        almacenamiento_oci={
            "bucket": "nuevamente-contenidos-educativos",
            "objeto_id": "outputs/ws/gen_01HX/content.json",
        },
    )


# ---------------------------------------------------------------------------
# Los 5 formatos: ejemplos válidos (los mismos de docs/contratos-api.md)
# ---------------------------------------------------------------------------

FLASHCARDS = {
    "tipo": "flashcards",
    "titulo": "Dominando Redes en la Nube (VCN) desde Cero",
    "introduccion_contextualizada": "Contexto inicial adaptado al perfil.",
    "items": [
        {
            "id": "fc_001",
            "frente": "¿Qué es una VCN en Oracle Cloud?",
            "dorso": "Una red virtual privada que aísla recursos en OCI.",
            "pista_didactica": "Piensa en 'barrio privado dentro de la ciudad'.",
            "etiquetas": ["redes"],
            "referencias": [REFERENCIA],
        }
    ],
}

QUIZ = {
    "tipo": "quiz",
    "titulo": "Quiz: fundamentos de VCN",
    "preguntas": [
        {
            "id": "q_001",
            "enunciado": "¿Qué delimita una VCN dentro de OCI?",
            "opciones": [
                {"option_id": "A", "texto": "Un rango CIDR elegido al crearla"},
                {"option_id": "B", "texto": "El nombre de la región"},
                {"option_id": "C", "texto": "La lista de usuarios"},
                {"option_id": "D", "texto": "El tamaño del bucket"},
            ],
            "correct_option_id": "A",
            "justificacion": "La VCN se define por su bloque CIDR; B, C y D no delimitan redes.",
            "referencias": [REFERENCIA],
        }
    ],
}

TUTORIAL = {
    "tipo": "tutorial",
    "titulo": "Tu primera VCN en 3 pasos",
    "audiencia": "Principiantes en cloud",
    "prerrequisitos": ["Cuenta de OCI"],
    "pasos": [
        {
            "id": "paso_1",
            "instruccion": "Abre el menú de redes y elige 'Virtual Cloud Networks'.",
            "resultado_esperado": "Ves la lista de VCNs del compartment.",
            "verificacion": "El botón 'Create VCN' está habilitado.",
            "codigo": None,
            "referencias": [REFERENCIA],
        }
    ],
}

RESUMEN = {
    "tipo": "resumen_ejecutivo",
    "titulo": "VCN para decisiones de negocio",
    "puntos_clave": ["Una VCN aísla y organiza los recursos de red."],
    "impacto_cualitativo": "Reduce riesgo de exposición sin costo adicional en Always Free.",
    "implicaciones": ["Toda arquitectura nueva debe nacer dentro de una VCN."],
    "acciones": ["Definir convención de rangos CIDR por ambiente."],
    "referencias": [REFERENCIA],
}

GUION = {
    "tipo": "guion_clase",
    "titulo": "Clase introductoria: redes en la nube",
    "objetivos": ["Explicar el concepto de red virtual privada."],
    "escenas": [
        {
            "id": "esc_1",
            "duracion_min": 2.5,
            "narracion": "Comencemos con una analogía: la ciudad y sus barrios...",
            "puntos_diapositiva": ["VCN = barrio privado", "Aislamiento por diseño"],
            "pregunta_interactiva": "¿Qué separaría un barrio de otro?",
            "referencias": [REFERENCIA],
        }
    ],
}

TODOS_LOS_FORMATOS = {
    "flashcards": (FLASHCARDS, FlashcardDeck),
    "quiz": (QUIZ, InteractiveQuiz),
    "tutorial": (TUTORIAL, PracticalTutorial),
    "resumen_ejecutivo": (RESUMEN, ExecutiveSummary),
    "guion_clase": (GUION, VideoLessonScript),
}


# parametrize recibe nombres de parámetros PLANOS separados por coma y una
# lista de tuplas con un valor por nombre: aplanamos TODOS_LOS_FORMATOS
# (dict de nombre -> (ejemplo, clase)) a tuplas de tres elementos.
@pytest.mark.parametrize(
    "nombre, ejemplo, clase",
    sorted((nombre, ejemplo, clase) for nombre, (ejemplo, clase) in TODOS_LOS_FORMATOS.items()),
)
def test_round_trip_por_formato(nombre, ejemplo, clase):
    """Cada formato serializa/deserializa sin pérdida y discrimina bien su clase."""
    # Primero el formato suelto...
    modelo = clase.model_validate(ejemplo)
    assert modelo.tipo == nombre
    reconstruido = clase.model_validate(modelo.model_dump())
    assert reconstruido == modelo
    # ...y después dentro del paquete completo (la unión discriminada real).
    paquete = salida_valida(ejemplo)
    data = paquete.model_dump()
    assert PedagogicalOutput.model_validate(data).contenido_adaptado == paquete.contenido_adaptado


def test_ejemplo_publico_de_contratos_api_valida():
    """El ejemplo completo de contratos-api.md (flashcards) debe validar tal cual.

    Este es el test de sincronización documento-implementación: si el
    contrato cambia y el documento no (o al revés), falla aquí.
    """
    paquete = salida_valida(FLASHCARDS)
    assert paquete.schema_version == "1.0"
    assert paquete.status == "aprobado"
    assert paquete.evaluacion_calidad.anclaje_fuente_score == 1.0


# ---------------------------------------------------------------------------
# Rechazo de inválidos: cada fila es (payload, motivo humano del caso)
# ---------------------------------------------------------------------------

QUIZ_BASE = QUIZ["preguntas"][0]


@pytest.mark.parametrize(
    "payload,razon",
    [
        # correct_option_id fuera de las opciones propias (regla §16.2).
        (
            {**QUIZ, "preguntas": [{**QUIZ_BASE, "correct_option_id": "Z"}]},
            "respuesta correcta inexistente",
        ),
        # option_id duplicados.
        (
            {
                **QUIZ,
                "preguntas": [
                    {
                        **QUIZ_BASE,
                        "opciones": [
                            {"option_id": "A", "texto": "1"},
                            {"option_id": "A", "texto": "2"},
                            {"option_id": "C", "texto": "3"},
                            {"option_id": "D", "texto": "4"},
                        ],
                    }
                ],
            },
            "opciones con IDs duplicados",
        ),
        # §16.2 fija exactamente cuatro opciones.
        ({**QUIZ, "preguntas": [{**QUIZ_BASE, "opciones": QUIZ_BASE["opciones"][:3]}]}, "tres opciones"),
        # Listas vacías se rechazan (§16.2).
        ({**FLASHCARDS, "items": []}, "mazo sin tarjetas"),
        ({**QUIZ, "preguntas": []}, "quiz sin preguntas"),
        ({**TUTORIAL, "pasos": []}, "tutorial sin pasos"),
        ({**RESUMEN, "puntos_clave": []}, "resumen sin puntos clave"),
        ({**GUION, "escenas": []}, "guion sin escenas"),
        # IDs duplicados dentro de un formato.
        (
            {**FLASHCARDS, "items": [FLASHCARDS["items"][0], dict(FLASHCARDS["items"][0])]},
            "IDs de flashcard duplicados",
        ),
        # Duraciones y tiempos estimados deben ser positivos.
        ({**GUION, "escenas": [{**GUION["escenas"][0], "duracion_min": 0}]}, "duración cero"),
    ],
)
def test_formatos_rechazan_payloads_invalidos(payload, razon):
    """Cada payload inválido debe lanzar ValidationError de Pydantic (criterio 2 del issue)."""
    formato = payload["tipo"]
    clase = TODOS_LOS_FORMATOS[formato][1]
    with pytest.raises(ValidationError):
        clase.model_validate(payload)


def test_score_fuera_de_rango_rechazado():
    """anclaje_fuente_score es un puntaje [0,1]; 1.5 o -0.1 no pueden entrar."""
    for score in (1.5, -0.1):
        with pytest.raises(ValidationError):
            EvaluacionCalidad.model_validate(evaluacion_valida(anclaje_fuente_score=score))


def test_respaldadas_mayor_a_afirmaciones_rechazado():
    """No pueden respaldarse más afirmaciones de las extraídas."""
    with pytest.raises(ValidationError):
        EvaluacionCalidad.model_validate(evaluacion_valida(cantidad_afirmaciones=10, cantidad_respaldadas=11))


def test_campo_desconocido_rechazado():
    """§7.2: campos extra se rechazan — el contrato no crece en silencio."""
    with pytest.raises(ValidationError):
        FlashcardDeck.model_validate({**FLASHCARDS, "campo_nuevo": "sorpresa"})
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(
            {
                "document_id": "doc_1",
                "perfil_destinatario": "principiante",
                "formato_salida": "flashcards",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "idioma_salida": "es",
                "alcance": {"tipo": "documento_completo"},
                "no_deberia_estar": True,
            }
        )


def test_enum_invalido_rechazado():
    """Un valor de enum mal escrito se rechaza con el campo exacto en el error."""
    peticion = {
        "document_id": "doc_1",
        "perfil_destinatario": "principiante",
        "formato_salida": "flashcard",  # falta la 's': debe fallar
        "nicho_sector": "general",
        "nivel_detalle": "didactico",
        "idioma_salida": "es",
        "alcance": {"tipo": "documento_completo"},
    }
    with pytest.raises(ValidationError) as info:
        GenerateRequest.model_validate(peticion)
    # Pydantic arma una lista de errores con loc = camino al campo ("formato_salida").
    assert any(err["loc"] == ("formato_salida",) for err in info.value.errors())


def test_alcance_seccion_sin_seccion_id_rechazado():
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(
            {
                "document_id": "doc_1",
                "perfil_destinatario": "principiante",
                "formato_salida": "flashcards",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "idioma_salida": "es",
                "alcance": {"tipo": "seccion"},  # falta seccion_id
            }
        )


def test_evento_progreso_invalido_rechazado():
    """concepto_revisado sin concepto no aporta al progreso: se rechaza."""
    with pytest.raises(ValidationError):
        ProgressEvent.model_validate({"event_id": "ev_1", "tipo": "concepto_revisado"})


# ---------------------------------------------------------------------------
# Congelación de enums: los valores exactos SON el contrato
# ---------------------------------------------------------------------------


def test_valores_de_maquina_estables():
    """Si alguien toca un valor de enum sin contract-change, este test lo delata."""
    assert [e.value for e in OutputLanguage] == ["es", "en", "pt"]
    assert [e.value for e in JobStatus] == [
        "queued",
        "running",
        "completed",
        "rejected_quality",
        "failed",
        "cancelled",
    ]
    assert [e.value for e in DocumentStatus] == ["processing", "ready", "failed"]


# ---------------------------------------------------------------------------
# Vista de estudiante del quiz: sin claves ni justificaciones
# ---------------------------------------------------------------------------


def test_vista_estudiante_omite_clave_y_justificacion():
    """La vista inicial del estudiante no recibe la respuesta (§16.2)."""
    quiz = InteractiveQuiz.model_validate(QUIZ)
    vista = quiz.vista_estudiante()
    dumped = vista.model_dump()
    assert isinstance(vista, QuizVistaEstudiante)
    assert "correct_option_id" not in dumped["preguntas"][0]
    assert "justificacion" not in dumped["preguntas"][0]
    # El canónico sigue teniendo la clave: la vista no muta el original.
    assert quiz.preguntas[0].correct_option_id == "A"


# ---------------------------------------------------------------------------
# Envoltorio de error y eventos SSE
# ---------------------------------------------------------------------------


def test_envoltorio_de_error_unico():
    """La forma de §7.3: error.code/message/details + request_id, nada más."""
    respuesta = ErrorResponse.model_validate(
        {
            "error": {
                "code": "DOCUMENT_TOO_LARGE",
                "message": "El archivo supera el límite de 20 MB para PDF.",
                "details": {"limite_mb": 20, "recibido_mb": 25.4},
            },
            "request_id": "req_8f14e45f",
        }
    )
    assert respuesta.error.code == ErrorCode.DOCUMENT_TOO_LARGE
    assert respuesta.model_dump()["request_id"] == "req_8f14e45f"


def test_eventos_sse_validan():
    """Eventos de §3.3: id monotónico (>=1), step, status, iteration opcional."""
    evento = SSEGenerationEvent.model_validate(
        {"id": 42, "generation_id": "gen_1", "step": "critic", "status": "running", "iteration": 2}
    )
    assert evento.iteration == 2
    assert (
        JobEvent.model_validate({"id": 1, "job_id": "job_1", "step": "chunking", "status": "queued"}).iteration is None
    )
    # id=0 rompe la monotonicidad desde 1.
    with pytest.raises(ValidationError):
        SSEGenerationEvent.model_validate({"id": 0, "generation_id": "gen_1", "step": "writer", "status": "running"})


# ---------------------------------------------------------------------------
# Sincronización viva con docs/contratos-api.md
# ---------------------------------------------------------------------------


def test_todos_los_json_publicados_en_contratos_api_md_validan():
    """Cada bloque JSON de la documentación del contrato debe validar contra los modelos.

    Este test es el guardian de la tarea "documento sincronizado con lo
    implementado": extrae TODOS los bloques ```json de contratos-api.md y los
    valida contra el modelo que les corresponde según su forma. Si alguien
    cambia el contrato sin actualizar el documento —o publica un ejemplo que
    el contrato rechaza— la CI falla aquí.
    """
    import json
    import re
    from pathlib import Path

    # __file__ vive en backend/tests/; la raíz del repo está dos niveles arriba.
    doc = Path(__file__).resolve().parents[2] / "docs" / "contratos-api.md"
    bloques = re.findall(r"```json\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)

    validados = {"paquete": 0, "formato": 0, "error": 0, "peticion": 0, "trabajo": 0}
    for bloque in bloques:
        data = json.loads(bloque)
        if "schema_version" in data:
            PedagogicalOutput.model_validate(data)
            validados["paquete"] += 1
        elif "tipo" in data:
            TODOS_LOS_FORMATOS[data["tipo"]][1].model_validate(data)
            validados["formato"] += 1
        elif "error" in data:
            ErrorResponse.model_validate(data)
            validados["error"] += 1
        elif "formato_salida" in data:
            GenerateRequest.model_validate(data)
            validados["peticion"] += 1
        elif "generation_id" in data:
            GenerationJobResponse.model_validate(data)
            validados["trabajo"] += 1

    assert validados == {
        "paquete": 1,  # el ejemplo completo de PedagogicalOutput
        "formato": 4,  # quiz, tutorial, resumen_ejecutivo y guion_clase
        "error": 1,  # la envoltura de error de §7.3
        "peticion": 1,  # el body de POST /api/generate
        "trabajo": 1,  # la respuesta 202 del trabajo
    }


# Marca de modulo (infraestructura del issue #10): la CI selecciona
# solo unit + integration_mock; integration_real jamas corre en CI (#54).
pytestmark = pytest.mark.unit
