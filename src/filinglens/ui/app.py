"""Streamlit analyst dashboard. Run with: `uv run streamlit run src/filinglens/ui/app.py`.

Thin presentation only: all data comes from `filinglens.ui.viewmodels` over the composition
root. The UI never fetches SEC data, parses filings or calculates financial metrics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

from filinglens import __version__
from filinglens.bootstrap import Container
from filinglens.config import get_settings
from filinglens.domain.errors import FilingLensError
from filinglens.ui import viewmodels as vm

FORMS = ["10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"]


def _container() -> Container:
    if "container" not in st.session_state:
        st.session_state["container"] = Container.from_settings(get_settings())
    c: Container = st.session_state["container"]
    return c


def _issuer_panel(c: Container) -> int | None:
    st.subheader("Issuer")
    query = st.text_input("Ticker, CIK, EXCHANGE:TICKER or company name", key="issuer_query")
    if not query:
        return st.session_state.get("cik")
    view = vm.search_issuers(c, query)
    if view.status in ("not_found", "unavailable"):
        st.warning(view.message)
        return None
    if view.status == "ambiguous":
        st.info(view.message)
        labels = [ch.label for ch in view.choices]
        picked = st.radio("Candidates", labels, index=None, key="issuer_pick")
        if picked is None:
            return None
        cik = view.choices[labels.index(picked)].cik
    else:
        st.success(view.message)
        cik = view.choices[0].cik
    st.session_state["cik"] = cik
    return cik


def _timeline_panel(c: Container, cik: int) -> list[vm.TimelineRow]:
    st.subheader("Filing timeline")
    forms = st.multiselect("Forms", FORMS, default=["10-K", "10-Q", "8-K"], key="forms")
    rows = vm.filing_timeline(c, cik, forms=set(forms) if forms else None)
    if rows:
        st.dataframe([r.__dict__ for r in rows], hide_index=True, use_container_width=True)
    else:
        st.info("No filings stored for this issuer yet.")
    return rows


def _run_panel(c: Container, cik: int, timeline: list[vm.TimelineRow]) -> None:
    st.subheader("Comparison runs")
    accessions = [r.accession for r in timeline]
    with st.form("create_run"):
        focal = st.selectbox("Focal filing", accessions, index=0 if accessions else None)
        as_of_date = st.date_input("As of (UTC)", value=datetime.now(UTC).date())
        submitted = st.form_submit_button("Create run")
    if submitted:
        as_of = datetime.combine(as_of_date, datetime.max.time(), tzinfo=UTC)
        try:
            run = c.runs.create(cik=cik, as_of=as_of, focal_accession=focal)
            st.success(f"Run {run.run_id} created.")
        except FilingLensError as exc:
            st.error(str(exc))
    rows = vm.run_rows(c, cik)
    for r in rows:
        line = f"`{r.run_id}` · **{r.state}** · focal {r.focal or '—'} · as of {r.as_of}"
        if r.needs_attention:
            st.error(f"{line} — {r.message}")
        else:
            st.write(line)
    if rows:
        run_id = st.selectbox("Inspect run", [r.run_id for r in rows], key="run_pick")
        if run_id:
            _findings_panel(c, run_id)


def _findings_panel(c: Container, run_id: str) -> None:
    st.subheader("Top findings")
    col1, col2 = st.columns(2)
    severity = col1.selectbox("Minimum severity", ["low", "medium", "high"], key="sev")
    section_filter = col2.text_input("Section/item filter (e.g. 1A, 7)", key="sec")
    findings = vm.top_findings(
        c, run_id, min_severity=severity, sections={section_filter} if section_filter else None
    )
    if not findings:
        st.info("No findings for this run and filter.")
    for f in findings:
        with st.expander(f"[{f.severity}] {f.finding_type} — {f.summary}"):
            st.write(f"Status: {f.status} · confidence {f.confidence:.2f}")
            if f.why_it_matters:
                st.write(f.why_it_matters)
            for e in f.evidence:
                if e.missing:
                    st.warning(f"Evidence {e.evidence_id} is missing from this run.")
                else:
                    st.caption(
                        f"{e.evidence_id} · {e.accession} · {e.section or '—'} · {e.locator}"
                    )
                    st.markdown(f"> {e.text}")


def render(container: Any = None) -> None:
    st.set_page_config(page_title="FilingLens", layout="wide")
    st.title("FilingLens")
    st.caption(
        f"SEC filing intelligence · v{__version__} · research priority is not legal materiality"
    )
    c = container or _container()
    cik = _issuer_panel(c)
    if cik is None:
        return
    timeline = _timeline_panel(c, cik)
    _run_panel(c, cik, timeline)


if __name__ == "__main__":
    render()
