import streamlit as st


def aplicar_tema():
    """Inyecta CSS para fuentes Inter/JetBrains, bordes hairline y accesibilidad."""
    css = """
    <style>
    /* Importar fuentes si no se auto-hospedan inicialmente */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    code {
        font-family: 'JetBrains Mono', monospace;
    }

    /* Bordes hairline y tarjetas carbón */
    .stCard, div[data-testid="stExpander"] {
        background-color: #0f1011 !important;
        border: 1px solid #23252a !important;
        border-radius: 8px;
    }

    /* Foco visible para navegación por teclado (Accesibilidad) */
    *:focus-visible {
        outline: 2px solid #5e6ad2 !important;
        outline-offset: 2px;
    }

    /* Respeto por preferencias de movimiento del usuario */
    @media (prefers-reduced-motion: reduce) {
        * {
            animation: none !important;
            transition: none !important;
        }
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
