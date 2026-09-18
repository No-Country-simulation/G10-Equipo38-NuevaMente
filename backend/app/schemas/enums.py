"""Enumeraciones del contrato v1 (issue #03, referencia §16.1 y §17.3).

Por qué este archivo existe: los "enums" son listas cerradas de valores que
viajan por la red (por ejemplo ``"flashcards"`` dentro de un JSON). Fijarlos
aquí hace que backend, frontend y tests compartan EXACTAMENTE los mismos
códigos; si alguien escribe ``"flashcard"`` (sin "s"), Pydantic lo rechaza en
la frontera en vez de corromper datos más adentro.

Regla de oro del proyecto (§16.1 y §17.3): estos son VALORES DE MÁQUINA
estables — jamás se traducen ni se muestran crudos al usuario. Las etiquetas
visibles («Flashcards de Memorización», «Principiante / Transición de
Carrera»...) viven en los catálogos i18n del frontend (issue #06), separadas
de este contrato.

Congelación: este archivo es parte del contrato v1 congelado por el issue
#03. Cualquier cambio posterior (agregar o renombrar un valor) exige un PR
etiquetado ``contract-change`` con revisión de los carriles API+UI+AGT+RAG,
porque rompería clientes y documentos ya persistidos.

Nota técnica: heredamos de ``str`` además de ``Enum`` para que al serializar
a JSON el valor salga como string plano ("quiz") y no como objeto raro de
Python; es el patrón estándar con Pydantic v2 y FastAPI.
"""

from enum import Enum


class RecipientProfile(str, Enum):
    """¿Para QUIÉN se adapta el contenido? Fija vocabulario y conocimientos previos (§16.1).

    El orden no es casual: va de menos a más conocimiento técnico asumido.
    Ejemplo de etiqueta i18n para ``principiante``: «Principiante / Transición
    de Carrera» (la etiqueta vive en frontend/i18n, no aquí).
    """

    PRINCIPIANTE = "principiante"
    JUNIOR_SSR = "junior_ssr"
    LIDER_TECNICO = "lider_tecnico"
    EJECUTIVO = "ejecutivo"


class PedagogicalFormat(str, Enum):
    """¿QUÉ tipo de material educativo se genera? Los 5 formatos de §16.2.

    Cada valor corresponde a un modelo tipado en ``pedagogical.py`` y es el
    discriminador de la unión ``contenido_adaptado``: el campo ``tipo`` del
    JSON decide a cuál de los 5 modelos se parsea.
    """

    TUTORIAL = "tutorial"
    FLASHCARDS = "flashcards"
    QUIZ = "quiz"
    RESUMEN_EJECUTIVO = "resumen_ejecutivo"
    GUION_CLASE = "guion_clase"


class IndustryNiche(str, Enum):
    """¿En QUÉ contexto sectorial se ejemplifican los contenidos? (§16.1).

    El nicho NO cambia la estructura del paquete, solo los ejemplos y la
    terminología que el Writer debe usar (issue #22, biblioteca de prompts).
    """

    FINTECH = "fintech"
    SALUD = "salud"
    ECOMMERCE = "ecommerce"
    GENERAL = "general"


class DetailLevel(str, Enum):
    """¿Con cuánta PROFUNDIDAD técnica se explica? (§16.1).

    Independiente del perfil: el perfil fija el lenguaje, el detalle fija la
    profundidad. Una combinación como ejecutivo + tecnico_profundo explica
    decisiones y trade-offs en lenguaje accesible (§16.1).
    """

    DIDACTICO = "didactico"
    PRACTICO = "practico"
    TECNICO_PROFUNDO = "tecnico_profundo"


class OutputLanguage(str, Enum):
    """Idioma de SALIDA del contenido educativo (§17).

    El idioma de ORIGEN del documento es metadata detectada y nunca
    sobreescribe la elección explícita del usuario (§17.2). La detección
    puede devolver "mixto"; por eso el campo metadatos.idioma_origen NO usa
    este enum sino un Literal propio que incluye "mixto".
    """

    ES = "es"
    EN = "en"
    PT = "pt"


class JobStatus(str, Enum):
    """Ciclo de vida de un trabajo asíncrono (generación, ingestión, chat, glosario).

    Estados terminales: completed, rejected_quality, failed y cancelled.
    Reglas de contrato críticas (§3.3 y §16.3):

    - ``completed`` SOLO se emite tras confirmar la persistencia en OCI; un
      error de subida deja el trabajo en ``failed`` (código
      STORAGE_UNAVAILABLE), nunca en completed.
    - ``rejected_quality`` significa que el contenido no superó la revisión
      del Critic (score de anclaje o rúbrica pedagógica): es un resultado
      del trabajo, no un error de la consulta (HTTP 200 al consultarlo).
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED_QUALITY = "rejected_quality"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocumentStatus(str, Enum):
    """Ciclo de vida de un documento cargado (§3.2 paso 6).

    ``ready`` es el único estado desde el que POST /api/generate acepta
    generar (§7.2: "la generación solo acepta documentos ready").
    """

    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
