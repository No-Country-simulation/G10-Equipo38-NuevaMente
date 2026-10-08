import streamlit as st
from api_client import APIClient, APIError
from i18n import CATALOGOS, t

from components.errors import mostrar_error_ui
from components.history import render_historial
from components.session import limpiar_sesion_local


def render_sidebar(api_client: APIClient, idioma_actual: str):
    """Renderiza el panel lateral con controles de idioma, sesión e historial."""
    with st.sidebar:
        st.title(t("comun.app_nombre", idioma_actual))

        _render_selector_idioma(idioma_actual)

        st.divider()

        st.markdown(f"### {t('sidebar.titulo', idioma_actual)}")
        st.markdown(f"### {t('upload.titulo', idioma_actual)}")

        # Acciones de Sesión Activa
        if st.session_state.get("session_token"):
            st.divider()
            _render_acciones_sesion(api_client, idioma_actual)

        st.divider()
        # Componente de Historial
        render_historial(idioma_actual)

    # Evaluar si se debe desplegar el modal de confirmación
    if st.session_state.get("accion_confirmar"):
        _procesar_modal_pendiente(api_client, idioma_actual)


def _render_selector_idioma(idioma_actual: str):
    """Maneja el selector de idioma de la interfaz (UI)."""
    nuevo_idioma = st.selectbox(
        t("sidebar.idioma_ui", idioma_actual),
        options=CATALOGOS.keys(),
        index=list(CATALOGOS.keys()).index(idioma_actual),
        format_func=lambda x: t(f"idioma.{x}", idioma_actual),
    )

    # Actualizar idioma si el usuario lo cambia
    if nuevo_idioma != idioma_actual:
        st.session_state.idioma_ui = nuevo_idioma
        # Recarga la interfaz en caliente para aplicar traducciones
        st.rerun()


def _render_acciones_sesion(api_client: APIClient, idioma_actual: str):
    """Renderiza el botón para cerrar sesión y el menú desplegable de configuración."""
    token = st.session_state.session_token

    if st.button(t("sidebar.cerrar_sesion", idioma_actual), use_container_width=True):
        try:
            api_client.close_session(token)
        except APIError as e:
            mostrar_error_ui(e, idioma_actual)
        else:
            limpiar_sesion_local()
            st.session_state.mostrando_recuperacion = True
            st.rerun()

    with st.expander("⚙️ " + t("nav.configuracion", idioma_actual)):
        if st.button(t("onboarding.rotar_codigo", idioma_actual), use_container_width=True):
            st.session_state.accion_confirmar = "rotar"
            st.rerun()

        st.divider()

        if st.button(t("onboarding.borrar_espacio", idioma_actual), type="primary", use_container_width=True):
            st.session_state.accion_confirmar = "borrar"
            st.rerun()


def _procesar_modal_pendiente(api_client: APIClient, idioma_actual: str):
    """Maneja la ejecución del callback para el modal de confirmación."""
    token = st.session_state.get("session_token")
    accion = st.session_state.get("accion_confirmar")

    # Determinar título y mensaje según la acción a confirmar
    if accion == "rotar":
        titulo_modal = t("onboarding.rotar_codigo", idioma_actual)
        mensaje_modal = t("onboarding.rotar_confirmacion", idioma_actual)
    else:
        titulo_modal = t("onboarding.borrar_espacio", idioma_actual)
        mensaje_modal = t("onboarding.borrar_confirmacion", idioma_actual)

    # Definición dinámica con título traducido
    @st.dialog(titulo_modal, dismissible=False)
    def _modal():
        st.warning(mensaje_modal)
        col_si, col_no = st.columns(2)
        with col_si:
            if st.button(t("comun.si", idioma_actual), type="primary", use_container_width=True):
                if accion == "rotar":
                    exito = _ejecutar_rotacion(api_client, token, idioma_actual)
                else:
                    exito = _ejecutar_borrado(api_client, token, idioma_actual)
                if exito:
                    st.session_state.accion_confirmar = None
                    st.rerun()
        with col_no:
            if st.button(t("comun.no", idioma_actual), use_container_width=True):
                st.session_state.accion_confirmar = None
                st.rerun()

    _modal()


def _ejecutar_rotacion(api_client: APIClient, token: str, idioma_actual: str) -> bool:
    try:
        res = api_client.rotate_recovery_code(token)
        st.session_state.session_token = res["token"]
        st.session_state.recovery_code = res["recovery_code"]
        return True
    except APIError as e:
        mostrar_error_ui(e, idioma_actual)
        return False


def _ejecutar_borrado(api_client: APIClient, token: str, idioma_actual: str) -> bool:
    try:
        api_client.delete_workspace(token)
        limpiar_sesion_local()
        st.session_state.aviso_sesion = "onboarding.borrado_recibido"
        return True
    except APIError as e:
        mostrar_error_ui(e, idioma_actual)
        return False
