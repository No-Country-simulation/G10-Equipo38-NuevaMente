---
name: grill-with-docs
description: Conducts an intense, structured Socratic interview using design trees and question frontiers to stress-test architecture, requirements, and features, while simultaneously capturing domain glossary terms in CONTEXT.md and Architectural Decision Records (ADRs). Use when clarifying requirements, kicking off a major feature, or stress-testing a technical plan.
---

# Grill with Docs

## Overview

A disciplined, structured interview technique to stress-test architecture, requirements, and decisions before writing code, while actively generating project documentation (`CONTEXT.md` glossary and `docs/adr/` Architecture Decision Records) as consensus forms.

Instead of passive Q&A or unvetted assumptions, this skill maps decisions as a **Design Tree** and resolves them in systematic **Rounds** across the **Question Frontier**, locking down concrete terminology and irreversible decisions into living markdown documentation.

## When to Use

- At the start of a major feature, pipeline design, or architectural pivot.
- When requirements are vague, overloaded, or potentially conflicting.
- When choosing between competing technical approaches (e.g., Chroma vs FAISS, LangGraph supervisor vs state machine, chunking strategy).
- When validating domain concepts and edge cases for NuevaMente (e.g., recipient personas, pedagogical formats, hallucination thresholds).

## Core Concepts

### 1. The Design Tree and the Question Frontier
Every decision branches into downstream choices. 
- **The Design Tree**: The complete hierarchy of interrelated choices required to ship a feature.
- **The Settled Core**: Decisions that have already been made and confirmed.
- **The Frontier**: Every decision whose prerequisites are settled, but has not yet been answered. These are the *only* questions that can be asked right now without guessing at unmade choices.
- **Downstream Questions**: Questions that depend on currently unsettled choices. These must wait for subsequent rounds.

### 2. The Living Documentation Rule
Never let a decision vanish into ephemeral chat history:
- When a new domain term or boundary is agreed upon, update or create `CONTEXT.md` immediately.
- When a major, hard-to-reverse architectural choice with trade-offs is resolved, write an ADR in `docs/adr/NNNN-<slug>.md`.

## The Grilling Workflow

```
Identify Scope ──→ Map Design Tree ──→ Compute Frontier ──→ Ask Round
                         ▲                                        │
                         │                                        ▼
                  Update Docs & Tree ◀─── Analyze User Answer ────┘
                         │
                  Frontier Empty?
                   ├── NO  ──→ Repeat Round
                   └── YES ──→ Synthesize & Transition to Spec/Implementation
```

### Protocol for Each Round

1. **Find facts yourself**: Never ask the user for facts obtainable from the codebase, git logs, or repository files. Inspect the code first. Ask only for intent, preferences, business constraints, and decisions.
2. **Present the entire current frontier in one structured block**: Number each question, provide explicit context, and offer your **recommended answer** with brief rationale.
3. **Wait for user input**: Do not make assumptions on unsettled branches while waiting.

#### Question Format Template

```markdown
❓ **Q1** - **<Decision Area / Title>**: <Concise context explaining why this decision is at the frontier and what trade-offs exist.>
- Option A: ...
- Option B: ...
➡️ **Recommended**: Option A because <precise technical justification>.

---

❓ **Q2** - **<Decision Area / Title>**: <Context and implications.>
- Option A: ...
- Option B: ...
➡️ **Recommended**: Option B because <precise technical justification>.
```

### Documentation Actions During Grilling

#### A. Updating `CONTEXT.md`
When a domain term is clarified or disambiguated:
1. Define what it IS in 1-2 tight sentences.
2. Explicitly specify terms to avoid under `_Avoid_`.
3. Example for NuevaMente:
```markdown
**Anclaje Fuente Score**:
Métrica cuantitativa (0.0 a 1.0) que mide la fidelidad fáctica de un contenido generado respecto a los fragmentos del documento fuente, calculada mediante el algoritmo de entailment de Ragas.
_Avoid_: Score de alucinación, similitud de embeddings, confianza general.
```

#### B. Recording Architecture Decision Records (ADRs)
Create an ADR in `docs/adr/NNNN-<slug>.md` if and only if the decision satisfies all three criteria:
1. **Hard to reverse**: High migration or refactoring cost.
2. **Surprising without context**: A future engineer might question why this alternative was chosen.
3. **Result of a real trade-off**: Genuine alternatives were evaluated.

ADR Structure:
```markdown
# NNNN - {Short Title}

## Context
{What was the problem, requirement, or constraint?}

## Decision
{What did we choose and why?}

## Status
Accepted

## Consequences
{Key benefits and trade-offs or limitations accepted.}
```

## Exit Condition

The session ends when the frontier is empty:
1. All branches of the design tree have been explored and settled.
2. All new terms are recorded in `CONTEXT.md`.
3. All critical architectural choices have corresponding ADRs.
4. The user confirms the final shared understanding.
