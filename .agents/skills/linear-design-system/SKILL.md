---
name: linear-design-system
description: Implements the Linear-inspired dark design system for NuevaMente. Provides design tokens, typography, surfaces, hairline borders, and specialized Streamlit/Gradio UI component styles (cards, metric gauges, flashcards, interactive quizzes, badges) to create a modern, high-craft, award-winning user experience.
---

# Linear Design System

## Overview

A high-craft, near-black, software-craft visual design system adapted from Linear's iconic product design. Built around an ultra-deep canvas (`#010102`), layered charcoal surfaces (`#0f1011`, `#141516`), hairline borders (`#23252a`), high-contrast typography (`#f7f8f8`), and the signature lavender-blue primary accent (`#5e6ad2`).

This system ensures that **NuevaMente** stands out visually in hackathon demonstrations with an interface that looks like a precision engineering tool rather than a generic boilerplate Streamlit/Gradio app.

## When to Use

- When designing, building, or restyling Streamlit, Gradio, or web frontend interfaces for NuevaMente.
- When creating UI components: document ingestion panels, recipient & format selector chips, RAG generation cards, real-time quiz widgets, flashcards, and fidelity score gauges.
- When injecting custom CSS or configuring theme tokens in `.streamlit/config.toml`.

---

## 1. Design Tokens

### Color Palette

| Token | Hex | Role |
|---|---|---|
| `canvas` | `#010102` | Deepest root background |
| `surface-1` | `#0f1011` | Primary cards, panels, sidebar background |
| `surface-2` | `#141516` | Nested cards, input containers, active states |
| `surface-3` | `#18191a` | Hover states, secondary buttons |
| `surface-hover` | `#222429` | Interactive element hover background |
| `hairline` | `#23252a` | Subtle 1px borders, separators |
| `hairline-strong` | `#34343a` | Focused card borders, highlighted separators |
| `primary` | `#5e6ad2` | Linear signature lavender-blue accent |
| `primary-hover` | `#828fff` | Primary button hover state |
| `primary-focus` | `#5e69d1` | Focus rings, active accents |
| `ink` | `#f7f8f8` | Primary text, titles, headings |
| `ink-muted` | `#d0d6e0` | Secondary body text, descriptions |
| `ink-subtle` | `#8a8f98` | Labels, captions, timestamps, meta tags |
| `ink-tertiary` | `#62666d` | Placeholders, disabled states |
| `semantic-success` | `#27a644` | High fidelity (>0.85), correct quiz answer |
| `semantic-warning` | `#f59e0b` | Medium fidelity (0.70-0.84), review needed |
| `semantic-error` | `#ef4444` | Low fidelity (<0.70), error states, wrong answer |
| `semantic-info` | `#38bdf8` | OCI Cloud badges, technical hints |

### Typography

- **Headings & Display**: `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
  - Weight: 600 (Semi-bold) or 700 (Bold)
  - Letter spacing: `-0.02em` (measured negative tracking for crisp display)
- **Body & Controls**: `Inter, system-ui, sans-serif`
  - Weight: 400 (Regular) and 500 (Medium for buttons/labels)
  - Line height: `1.5`
- **Code & Metrics**: `JetBrains Mono, Menlo, Monaco, "Courier New", monospace`
  - Weight: 400 (Body) and 600 (Metric values)

### Spacing & Elevation

- **Radii**:
  - Small elements (badges, buttons, tags): `6px`
  - Cards & panels: `10px` or `12px`
  - Modals / drawers: `16px`
- **Borders**: Strictly `1px solid var(--hairline)`. Never heavy bevels.
- **Shadows**: Subtle diffuse shadows only (`box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5)`).

---

## 2. Streamlit Theme Integration

### `.streamlit/config.toml`

```toml
[theme]
primaryColor = "#5e6ad2"
backgroundColor = "#010102"
secondaryBackgroundColor = "#0f1011"
textColor = "#f7f8f8"
font = "sans serif"
```

### Injected Linear CSS Boilerplate

Use this helper in your Streamlit application:

```python
import streamlit as st

def apply_linear_theme():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

    /* Global typography and background */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        color: #f7f8f8;
        background-color: #010102;
    }
    
    /* Code font override */
    code, pre, .stCodeBlock {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* Container cards */
    .linear-card {
        background-color: #0f1011;
        border: 1px solid #23252a;
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1rem;
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .linear-card:hover {
        border-color: #34343a;
        box-shadow: 0 4px 24px rgba(94, 106, 210, 0.08);
    }

    /* Persona and Format Pills / Badges */
    .linear-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 500;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        background-color: #141516;
        border: 1px solid #23252a;
        color: #d0d6e0;
    }
    .linear-badge-accent {
        background-color: rgba(94, 106, 210, 0.12);
        border-color: rgba(94, 106, 210, 0.35);
        color: #828fff;
    }
    .linear-badge-success {
        background-color: rgba(39, 166, 68, 0.12);
        border-color: rgba(39, 166, 68, 0.35);
        color: #27a644;
    }
    .linear-badge-warning {
        background-color: rgba(245, 158, 11, 0.12);
        border-color: rgba(245, 158, 11, 0.35);
        color: #f59e0b;
    }

    /* Fidelity Metric Score Gauge */
    .fidelity-gauge {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
    }
    .fidelity-number {
        font-family: 'JetBrains Mono', monospace;
        font-size: 2.2rem;
        font-weight: 700;
        line-height: 1;
    }
    .fidelity-high { color: #27a644; }
    .fidelity-mid { color: #f59e0b; }
    .fidelity-low { color: #ef4444; }

    /* Flashcard UI */
    .flashcard {
        background-color: #141516;
        border: 1px solid #23252a;
        border-radius: 10px;
        padding: 1.5rem;
        margin-top: 0.75rem;
        border-left: 4px solid #5e6ad2;
    }
    .flashcard-q {
        font-size: 1rem;
        font-weight: 600;
        color: #f7f8f8;
        margin-bottom: 0.5rem;
    }
    .flashcard-a {
        font-size: 0.92rem;
        color: #d0d6e0;
        line-height: 1.55;
    }

    /* Custom Streamlit button styling */
    div.stButton > button:first-child {
        background-color: #5e6ad2;
        color: #ffffff;
        border: 1px solid #5e6ad2;
        border-radius: 6px;
        font-weight: 500;
        padding: 0.5rem 1.25rem;
        transition: all 0.15s ease;
    }
    div.stButton > button:first-child:hover {
        background-color: #828fff;
        border-color: #828fff;
        box-shadow: 0 0 16px rgba(94, 106, 210, 0.4);
    }
    </style>
    """, unsafe_allow_html=True)
```

---

## 3. Dedicated Component Patterns for NuevaMente

### A. Fidelity Scorecard Component
Displays `anclaje_fuente_score` calculated by the Ragas algorithm:
```python
def render_fidelity_score(score: float):
    if score >= 0.85:
        tier = "fidelity-high"
        label = "Alta Fidelidad (Fáctico)"
        badge_cls = "linear-badge-success"
    elif score >= 0.70:
        tier = "fidelity-mid"
        label = "Fidelidad Aceptable"
        badge_cls = "linear-badge-warning"
    else:
        tier = "fidelity-low"
        label = "Riesgo de Alucinación"
        badge_cls = "linear-badge"

    st.markdown(f"""
    <div class="linear-card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
            <span class="linear-badge linear-badge-accent">Mecanismo Anti-Alucinación</span>
            <span class="linear-badge {badge_cls}">{label}</span>
        </div>
        <div class="fidelity-gauge">
            <span class="fidelity-number {tier}">{score:.2f}</span>
            <span style="color: #8a8f98; font-size: 0.85rem;">/ 1.00 anclaje_fuente_score</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
```

### B. Interactive Quiz Card Component
For real-time evaluation with immediate feedback and pedagogical explanations:
```python
def render_quiz_question(idx: int, question: str, options: list[str], correct_idx: int, justification: str):
    st.markdown(f"""
    <div class="linear-card">
        <span class="linear-badge linear-badge-accent">Pregunta {idx + 1}</span>
        <h4 style="margin: 0.6rem 0 1rem 0; color: #f7f8f8;">{question}</h4>
    </div>
    """, unsafe_allow_html=True)
    # Streamlit radio / buttons with styled outcome
```

### C. OCI Always Free Badge
Signals compliance with zero-cost OCI cloud storage:
```python
st.markdown("""
<div class="linear-badge" style="border-color: #38bdf8; color: #38bdf8; margin-bottom: 0.5rem;">
    ☁️ OCI Object Storage Always Free ($0.00) · Bucket: nuevamente-contenidos-educativos
</div>
""", unsafe_allow_html=True)
```

---

## 4. Quality & Accessibility Rules

1. **Contrast Ratio**: White text (`#f7f8f8`) on dark canvas (`#010102`) gives a >19:1 contrast ratio, far exceeding WCAG AAA (7:1).
2. **Never Color Alone**: Always accompany semantic status colors (green/yellow/red) with descriptive text and icons.
3. **No Flashing Gradients**: Linear elegance comes from flat charcoal panels, hairline borders, and targeted accent colors. Avoid loud rainbow gradients.
