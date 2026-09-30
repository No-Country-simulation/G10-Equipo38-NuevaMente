import pages.home as home
import streamlit as st
from components.theme import aplicar_tema
from i18n import t

st.set_page_config(page_title=t("comun.app_nombre"), layout="wide", initial_sidebar_state="expanded")
aplicar_tema()

st.markdown('<style>[data-testid="stSidebarNav"] {display: none;}</style>', unsafe_allow_html=True)

home.render()
