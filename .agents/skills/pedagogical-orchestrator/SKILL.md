---
name: pedagogical-orchestrator
description: Orchestrates the multi-agent pedagogical adaptation graph in LangGraph (Router/Supervisor, Researcher, Writer, Critic) across 4 recipient profiles, 5 pedagogical formats, and 4 industry niches. Enforces strict Pydantic structured outputs and generates Anki deck exports (.apkg / CSV). Use when generating educational content, tuning agent prompts, or structuring pedagogical models.
---

# Pedagogical Orchestrator

## Overview

The cognitive engine of **NuevaMente**. Transforms ingested technical documents into pedagogically tailored educational assets. Orchestrated as a state graph with **LangGraph**, it coordinates four specialized agents (**Supervisor/Router**, **Researcher**, **Writer**, and **Critic**) with an automated reflection and feedback loop grounded by `anclaje_fuente_score`.

All outputs are validated against strict **Pydantic schemas** and support multi-format exports including Anki flashcard packages (`.apkg` and CSV).

## When to Use

- Orchestrating the end-to-end generation graph with LangGraph.
- Adapting technical content for different audience profiles or industries.
- Producing structured educational formats (Tutorials, Flashcards, Quizzes, Executive Summaries, Scripts).
- Formatting outputs for Anki export or real-time quiz evaluations.

---

## 1. Multi-Agent Decision Graph (LangGraph)

```
[Start] ──→ [Supervisor / Router]
                    │
                    ▼
           [Researcher Agent] ──→ (Vector Store Retrieval)
                    │
                    ▼
            [Writer Agent] ◀────────┐
                    │               │ Feedback Loop
                    ▼               │ (Score < 0.85 & iter < 3)
            [Critic Agent] ─────────┘
                    │
                    ▼ (Score >= 0.85 or iter limit reached)
           [Structured Finalizer] ──→ [Output to UI & OCI]
```

### Agent Roles

1. **Supervisor / Router**: Validates user parameters (`recipient_profile`, `pedagogical_format`, `niche`, `detail_level`), initializes graph state, and establishes generation constraints.
2. **Researcher**: Formulates targeted semantic queries based on the requested format and user persona, extracting the most relevant context chunks from the ChromaDB vector store.
3. **Writer**: Synthesizes educational material conforming strictly to the recipient persona's cognitive level, using industry-specific examples from the chosen niche.
4. **Critic**: Evaluates the pedagogical draft against the pedagogical rubric, executes the Ragas faithfulness verification (invoking `rag-and-grounding`), and routes back to the Writer if statements lack grounding or if clarity is insufficient.

---

## 2. Recipient Profiles & Adaptation Matrix

| Profile | Pedagogical Tone | Vocabulary & Jargon | Structure & Focus |
|---|---|---|---|
| **Principiante / Transición** | Empathetic, encouraging, accessible | Avoid unexplained jargon; use everyday analogies | Small digestible steps, foundational concepts, visual metaphors |
| **Desarrollador Jr / Semi Senior** | Practical, hands-on, engineering-focused | Idiomatic code, API signatures, debugging tips | Concrete code examples, common pitfalls, configuration steps |
| **Líder Técnico / Arquitecto** | Analytical, strategic, architectural | Design patterns, CAP theorem, trade-offs, SLAs | System diagrams, non-functional requirements, failure modes |
| **Gestor / Ejecutivo (No Técnico)**| High-level, value-driven, concise | Business impact, ROI, compliance, timelines | Executive summary, metrics, risk factors, strategic takeaways |

---

## 3. Pedagogical Formats & Pydantic Schemas

### Core Enums & Base Schemas

```python
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class RecipientProfile(str, Enum):
    BEGINNER = "Principiante / Transición de Carrera"
    JUNIOR_SSR = "Desarrollador Junior / Semi Senior"
    TECH_LEAD = "Líder Técnico / Arquitecto"
    EXECUTIVE = "Gestor / Ejecutivo (No Técnico)"

class PedagogicalFormat(str, Enum):
    TUTORIAL = "Guía Práctica Paso a Paso (Tutorial)"
    FLASHCARDS = "Flashcards de Memorización"
    QUIZ = "Quiz Interactivo con Justificaciones"
    SUMMARY = "Resumen Ejecutivo (TL;DR)"
    SCRIPT = "Guion de Clase / Video"

class IndustryNiche(str, Enum):
    FINTECH = "Fintech"
    HEALTHCARE = "Salud"
    ECOMMERCE = "E-commerce"
    GENERAL = "General"
```

### 1. Flashcards Schema & Anki Helper

```python
class Flashcard(BaseModel):
    front: str = Field(description="Question, term, or prompt on the front of the card")
    back: str = Field(description="Concise, factual explanation or answer on the back")
    tags: List[str] = Field(default_factory=list, description="Categorization tags (e.g. ['fintech', 'security'])")

class FlashcardDeck(BaseModel):
    deck_name: str
    recipient_profile: RecipientProfile
    cards: List[Flashcard]

def export_flashcards_to_anki_csv(deck: FlashcardDeck) -> str:
    """Generates standard Anki-importable TSV/CSV text."""
    lines = ["#separator:tab", "#html:true", "#tags column:3"]
    for card in deck.cards:
        tags_str = " ".join(card.tags)
        lines.append(f"{card.front}\t{card.back}\t{tags_str}")
    return "\n".join(lines)
```

### 2. Quiz Interactivo Schema

```python
class QuizOption(BaseModel):
    option_id: str = Field(description="A, B, C, or D")
    text: str = Field(description="Option text")

class QuizQuestion(BaseModel):
    question_id: int
    question: str
    options: List[QuizOption]
    correct_option_id: str = Field(description="The correct option_id (e.g. 'B')")
    explanation: str = Field(description="Detailed pedagogical justification of why the correct option is right and why others are wrong.")
    source_chunk_reference: Optional[str] = Field(description="Quote or chunk ID from source document")

class InteractiveQuiz(BaseModel):
    quiz_title: str
    recipient_profile: RecipientProfile
    questions: List[QuizQuestion]
```

### 3. Step-by-Step Tutorial Schema

```python
class TutorialStep(BaseModel):
    step_number: int
    title: str
    instruction: str
    code_snippet: Optional[str] = None
    expected_result: str
    verification_check: str

class PracticalTutorial(BaseModel):
    title: str
    target_audience: RecipientProfile
    prerequisites: List[str]
    steps: List[TutorialStep]
    summary_and_next_steps: str
```

### 4. Executive Summary Schema

```python
class ExecutiveSummary(BaseModel):
    title: str
    tldr_bullets: List[str] = Field(description="3-5 bullet points summarizing key takeaways")
    business_impact: str
    architectural_or_strategic_implications: str
    recommended_actions: List[str]
```

### 5. Unified Pedagogical Output Wrapper

```python
class PedagogicalOutput(BaseModel):
    title: str
    recipient_profile: RecipientProfile
    pedagogical_format: PedagogicalFormat
    niche: IndustryNiche
    anclaje_fuente_score: float = Field(ge=0.0, le=1.0)
    source_document: str
    content_json: dict = Field(description="Serialized payload corresponding to the specific format schema")
    content_markdown: str = Field(description="Formatted Markdown version ready for rendering in Streamlit/Gradio")
    created_at: str
```

---

## 4. LangGraph Implementation Blueprint

```python
from typing import TypedDict, List
from langgraph.graph import StateGraph, END

class AgentState(TypedDict):
    document_context: List[str]
    profile: str
    format: str
    niche: str
    research_notes: str
    draft_content: str
    critique: str
    anclaje_score: float
    iteration: int
    final_output: dict

def supervisor_node(state: AgentState):
    # Validates input configuration
    return {"iteration": 0}

def researcher_node(state: AgentState):
    # Formulates retrieval queries and gathers grounded context
    return {"research_notes": "Retrieved chunks relevant to user profile and format"}

def writer_node(state: AgentState):
    # Synthesizes tailored pedagogical content based on research notes and any previous critique
    return {"draft_content": "Synthesized educational text"}

def critic_node(state: AgentState):
    # Runs Ragas faithfulness check and pedagogical rubric
    # Calculates anclaje_fuente_score
    score = 0.92 # computed via rag-and-grounding
    return {"anclaje_score": score, "critique": "All statements verified against context", "iteration": state["iteration"] + 1}

def should_revise(state: AgentState):
    if state["anclaje_score"] < 0.85 and state["iteration"] < 3:
        return "writer"
    return "finalizer"

def finalizer_node(state: AgentState):
    # Builds Pydantic output model
    return {"final_output": {"status": "completed", "score": state["anclaje_score"]}}

# Graph construction
workflow = StateGraph(AgentState)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("writer", writer_node)
workflow.add_node("critic", critic_node)
workflow.add_node("finalizer", finalizer_node)

workflow.set_entry_point("supervisor")
workflow.add_edge("supervisor", "researcher")
workflow.add_edge("researcher", "writer")
workflow.add_edge("writer", "critic")
workflow.add_conditional_edges("critic", should_revise, {
    "writer": "writer",
    "finalizer": "finalizer"
})
workflow.add_edge("finalizer", END)

orchestrator_app = workflow.compile()
```

---

## 5. Non-Contradiction Guidelines

1. **Pydantic is Sovereign**: All agent outputs must pass Pydantic schema validation. If an LLM returns partial Markdown, the output node must serialize it cleanly into the matching schema.
2. **Grounding Over Fluency**: The Critic agent must never approve a response with high linguistic polish if `anclaje_fuente_score < 0.85`.
3. **Graceful Degradation**: If maximum iterations (3) are reached without passing threshold, the system returns the highest-scoring draft annotated with an explicit warning banner.
