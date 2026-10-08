import streamlit as st
from i18n import t


def render_historial(idioma_actual: str):
    """Componente para mostrar el historial en la barra lateral."""
    with st.expander(t("nav.historial", idioma_actual)):
        st.caption(t("historial.vacio", idioma_actual))
