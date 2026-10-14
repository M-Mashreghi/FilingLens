"""Comparison workspace page (renders a prepared `Workspace` from session state)."""

from __future__ import annotations

import streamlit as st

from filinglens.ui.workspace import ChangeRow, Workspace


def _change(c: ChangeRow) -> None:
    label = f"{c.kind} {c.block_kind}" + (" (moved)" if c.moved else "")
    with st.expander(f"{label} · {c.change_id}"):
        left, right = st.columns(2)
        left.caption(f"Comparator · {c.old_locator or '—'}")
        left.markdown(c.old_text or "_(none)_")
        right.caption(f"Current · {c.new_locator or '—'}")
        right.markdown(c.new_text or "_(none)_")
        for op, text in c.sentence_edits:
            st.markdown(f"{'➕' if op == 'added' else '➖'} {text}")
        if c.table_cells or c.table_rows_added or c.table_rows_removed:
            st.dataframe(
                [{"row": r, "column": col, "old": o, "new": n} for r, col, o, n in c.table_cells],
                hide_index=True,
            )
            if c.table_rows_added:
                st.write("Rows added: " + ", ".join(c.table_rows_added))
            if c.table_rows_removed:
                st.write("Rows removed: " + ", ".join(c.table_rows_removed))


def render(ws: Workspace) -> None:
    st.header("Filing comparison")
    a, b = st.columns(2)
    a.subheader("Current")
    a.json(ws.current.__dict__)
    b.subheader("Comparator")
    b.json(ws.comparator.__dict__ if ws.comparator else {"comparator": None})
    if ws.lineage:
        st.subheader("Amendment lineage")
        amendments = ", ".join(ws.lineage.amendments) or "none"
        st.write(
            f"Original: {ws.lineage.original} · amendments: {amendments}"
            f" · effective: {ws.lineage.effective}"
        )
    if ws.tabs:
        tabs = st.tabs([f"{t.title} ({t.added}+/{t.removed}-/{t.modified}~)" for t in ws.tabs])
        for tab, sec in zip(tabs, ws.tabs, strict=True):
            with tab:
                st.caption(f"Change magnitude {sec.magnitude:.2f}")
                for c in sec.changes:
                    _change(c)
    else:
        st.info("No textual changes in aligned sections.")
    if ws.eight_k:
        st.subheader("8-K items and exhibits")
        st.dataframe(
            [{"item": i, "title": t, "category": c} for i, t, c in ws.eight_k.items],
            hide_index=True,
        )
        st.dataframe(
            [{"exhibit": n, "status": s, "file": f} for n, s, f in ws.eight_k.exhibits],
            hide_index=True,
        )
    if ws.metrics:
        st.subheader("XBRL metrics and fact lineage")
        st.dataframe([m.__dict__ for m in ws.metrics], hide_index=True)
    if ws.findings:
        st.subheader("Findings → evidence")
        for f in ws.findings:
            targets = (
                "; ".join(f"{e} → section {s} / {c}" for e, s, c in f.targets) or "no linked change"
            )
            st.markdown(f"- **{f.finding_type}**: {f.summary} ({targets})")


if "workspace" in st.session_state:
    render(st.session_state["workspace"])
else:
    st.info("Open a run from the dashboard to load a comparison workspace.")
