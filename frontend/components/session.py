import streamlit as st
from i18n import IDIOMA_POR_DEFECTO


def inicializar_estado_sesion():
    """Establece los valores iniciales de sesión en st.session_state."""
    if "session_token" not in st.session_state:
        st.session_state.session_token = None
    if "workspace_id" not in st.session_state:
        st.session_state.workspace_id = None
    if "recovery_code" not in st.session_state:
        st.session_state.recovery_code = None
    if "idioma_ui" not in st.session_state:
        st.session_state.idioma_ui = IDIOMA_POR_DEFECTO
    if "mostrando_recuperacion" not in st.session_state:
        st.session_state.mostrando_recuperacion = False
    if "accion_confirmar" not in st.session_state:
        st.session_state.accion_confirmar = None
    if "error_sesion_invalida" not in st.session_state:
        st.session_state.error_sesion_invalida = False


def limpiar_sesion_local():
    """Limpia los tokens y datos de la sesión activa."""
    st.session_state.session_token = None
    st.session_state.workspace_id = None
    st.session_state.recovery_code = None
    st.session_state.mostrando_recuperacion = False
    st.session_state.accion_confirmar = None
    st.session_state.error_sesion_invalida = False


def manejar_sesion_invalida():
    """Invalida la sesión local y redirige al flujo de recuperación guardando el motivo."""
    limpiar_sesion_local()
    st.session_state.mostrando_recuperacion = True
    st.session_state.error_sesion_invalida = True
