import pages.home as home
import streamlit as st
from api_client import APIClient, APIError
from components.session import inicializar_estado_sesion
from components.sidebar import render_sidebar
from components.theme import aplicar_tema
from i18n import t

st.set_page_config(page_title=t("comun.app_nombre"), layout="wide", initial_sidebar_state="expanded")
aplicar_tema()

st.markdown('<style>[data-testid="stSidebarNav"] {display: none;}</style>', unsafe_allow_html=True)

api = APIClient()

inicializar_estado_sesion()
idioma_actual = st.session_state.idioma_ui

# Creación automática de espacio anónimo
if not st.session_state.session_token and not st.session_state.mostrando_recuperacion:
    try:
        res = api.create_workspace()
        st.session_state.session_token = res["token"]
        st.session_state.workspace_id = res["workspace_id"]
        st.session_state.recovery_code = res.get("recovery_code")
    except APIError as e:
        # Renderizar sidebar básica para permitir cambiar de idioma
        render_sidebar(api, idioma_actual)

        # Renderizar estado de error inicial sin onboarding
        st.header(t("onboarding.bienvenida_titulo", idioma_actual))

        titulo = t("errors.titulo", idioma_actual)
        mensaje = t(f"errors.{e.code}", idioma_actual)
        st.error(f"**{titulo}**: {mensaje}")

        etiqueta_detalles = t("comun.detalles_tecnicos", idioma_actual)
        with st.expander(etiqueta_detalles):
            st.code(f"Code: {e.code}\nRequest-ID: {e.request_id or 'N/A'}")

        st.divider()
        if st.button(t("comun.reintentar", idioma_actual), type="primary"):
            st.rerun()

        # Detener la ejecución para no renderizar home.py mientras la API está caída
        st.stop()

home.render(api, idioma_actual)
