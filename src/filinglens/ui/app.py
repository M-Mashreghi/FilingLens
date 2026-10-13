"""Streamlit shell. Run with: `uv run streamlit run src/filinglens/ui/app.py`."""

from __future__ import annotations

import streamlit as st

from filinglens import __version__


def render() -> None:
    st.set_page_config(page_title="FilingLens", layout="wide")
    st.title("FilingLens")
    st.caption(f"SEC filing intelligence · v{__version__}")


if __name__ == "__main__":
    render()
