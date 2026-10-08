"""Mensajes de API traducidos, sin cuerpos remotos ni credenciales en pantalla."""

import streamlit as st
from api_client import APIError
from i18n import CATALOGOS, t

from components.session import manejar_sesion_invalida


def mostrar_error_ui(error: APIError, idioma_actual: str) -> None:
    if error.status_code == 401 and not st.session_state.get("mostrando_recuperacion"):
        manejar_sesion_invalida()
        st.rerun()
    clave = f"errors.{error.code}"
    if clave not in CATALOGOS["es"]:
        clave = "errors.INTERNAL"
    st.error(f"**{t('errors.titulo', idioma_actual)}**: {t(clave, idioma_actual)}")
    if error.retry_after and error.retry_after.isdigit():
        st.caption(t("errors.reintentar_en", idioma_actual).format(segundos=error.retry_after))
    with st.expander(t("comun.detalles_tecnicos", idioma_actual)):
        st.code(f"Code: {error.code}\nRequest-ID: {error.request_id or 'N/A'}")
