"""Secure PDF PII Detection, Redaction and Search - Streamlit entry point.

Run with:  streamlit run app.py   (or double-click run.bat on Windows)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import streamlit as st

from pii_platform.pipeline import build_pipeline
from ui import views

st.set_page_config(page_title="PDF PII Redaction Platform", page_icon="\U0001F6E1\uFE0F", layout="wide")


@st.cache_resource(show_spinner="Starting local engine...")
def get_pipeline():
    return build_pipeline()


pipe = get_pipeline()

PAGES = {
    "Dashboard": views.dashboard,
    "Process document": views.process,
    "History": views.history,
    "Search": views.search,
    "About & limits": views.about,
}

with st.sidebar:
    st.title("\U0001F6E1\uFE0F PII Redaction")
    st.caption("Secure, local-first PDF PII detection, redaction and search")
    choice = st.radio("Navigate", list(PAGES), label_visibility="collapsed")
    st.divider()
    st.success("Local mode: no data leaves this computer", icon="\U0001F512")
    for w in dict.fromkeys(pipe.settings.warnings):
        st.warning(w)

PAGES[choice](pipe)
