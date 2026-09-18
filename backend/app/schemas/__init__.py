"""Contratos compartidos v1 (carril QAD, con revisión obligatoria de los
carriles API + UI + AGT + RAG).

Módulos previstos (§15, detalles en §16):
- enums.py: RecipientProfile (4), PedagogicalFormat (5), IndustryNiche (4),
  DetailLevel (3), OutputLanguage (es/en/pt) y JobStatus — valores de
  máquina estables; las etiquetas traducibles viven en frontend/i18n/.
- pedagogical.py: los 5 formatos (FlashcardDeck, InteractiveQuiz,
  PracticalTutorial, ExecutiveSummary, VideoLessonScript) con las
  validaciones de §16.2.
- responses.py: PedagogicalOutput con los campos exactos de §16.3.
- requests.py: GenerateRequest, UploadRequest, ChatRequest, GlossaryRequest
  y ProgressEvent (idempotente).

Congelados en v1 por el issue #03. Cualquier cambio posterior es un PR
etiquetado `contract-change` con actualización de docs/contratos-api.md en el
mismo PR (guía §2).
"""
