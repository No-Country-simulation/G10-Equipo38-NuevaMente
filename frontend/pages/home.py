import streamlit as st
from i18n import CATALOGOS, IDIOMA_POR_DEFECTO, t


def render():
    # Inicializar el idioma en session_state si no existe
    if "idioma_ui" not in st.session_state:
        st.session_state.idioma_ui = IDIOMA_POR_DEFECTO

    idioma_actual = st.session_state.idioma_ui

    # --- SIDEBAR (Barra lateral) ---
    with st.sidebar:
        st.title(t("comun.app_nombre", idioma_actual))

        # Selector de idioma de UI
        nuevo_idioma = st.selectbox(
            t("sidebar.idioma_salida", idioma_actual),
            options=CATALOGOS.keys(),
            index=list(CATALOGOS.keys()).index(idioma_actual),
            format_func=lambda x: t(f"idioma.{x}", idioma_actual),
        )

        # Actualizar idioma si el usuario lo cambia
        if nuevo_idioma != idioma_actual:
            st.session_state.idioma_ui = nuevo_idioma
            # Recarga la interfaz en caliente para aplicar traducciones
            st.rerun()

        st.divider()
        st.markdown(f"### {t('sidebar.titulo', idioma_actual)} (Placeholder)")
        st.markdown(f"### {t('upload.titulo', idioma_actual)} (Placeholder)")

        # Expander para el historial
        with st.expander(t("nav.historial", idioma_actual)):
            st.write(t("historial.vacio", idioma_actual))

    # --- ÁREA PRINCIPAL (Estado Vacío / Onboarding) ---
    st.header(t("onboarding.bienvenida_titulo", idioma_actual))
    st.write(t("onboarding.bienvenida_descripcion", idioma_actual))

    # 4 botones nativos de Streamlit para el onboarding
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        # Cargar un documento
        if st.button(t("upload.titulo", idioma_actual), use_container_width=True):
            pass
    with col2:
        # Pegar texto
        if st.button(t("upload.enviar_texto", idioma_actual), use_container_width=True):
            pass
    with col3:
        # Crear espacio (Demo)
        if st.button(t("onboarding.crear_espacio", idioma_actual), use_container_width=True):
            pass
    with col4:
        # Recuperar espacio
        if st.button(t("onboarding.recuperar_titulo", idioma_actual), use_container_width=True):
            pass
