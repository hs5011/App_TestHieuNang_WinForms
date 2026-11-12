"""Bước 9 - Biểu đồ."""
from __future__ import annotations

import hashlib

import streamlit as st

from ...charts.builder import CHART_TITLES, OVERVIEW_TITLES, build_all, build_overview
from ...models import Project
from ...scriptgen.profile import scenario_name
from ...storage import sub_dir
from .. import state
from ..components import section_titles
from .s08_results import get_rows, row_key, selected_rows


def get_charts(p: Project, rows: list[dict], force: bool = False):
    from ...monitor.store import data_files
    mon_sig = f"{p.monitor.enabled}:{sum(f.stat().st_mtime for f in data_files(p.id)):.0f}"   # vẽ lại khi có số liệu máy chủ mới
    key = f"charts_{p.id}_{mon_sig}_{'|'.join(row_key(r) for r in rows)}"
    if force or key not in st.session_state:
        with st.spinner("Đang vẽ biểu đồ..."):
            st.session_state[key] = build_all(p, rows, sub_dir(p.id, "charts"))
    return st.session_state[key]


def render(p: Project) -> None:
    st.header("9. Biểu đồ")
    if not any(r.status == "done" for r in p.runs):
        st.warning("Chưa có kết quả.")
        state.nav_buttons("results", None)
        return
    _, all_rows = get_rows(p)
    rows = selected_rows(p, all_rows)
    if not rows:
        st.warning("Không có dữ liệu – kiểm tra lựa chọn kịch bản ở Bước 8.")
        state.nav_buttons("results", None)
        return
    c_info, c_btn = st.columns([3, 1], vertical_alignment="center")
    c_info.info(f"Biểu đồ cho **{len(rows)}/{len(all_rows)} kịch bản** đã chọn ở Bước 8: "
                + ", ".join(sorted({r['uc_code'] for r in rows})))
    if c_btn.button("✏️ Đổi lựa chọn", width="stretch"):
        state.goto("results")
    if st.button("🔄 Vẽ lại"):
        get_charts(p, rows, force=True)
    per_row, cmp = get_charts(p, rows)
    sec = section_titles(9)
    if len(rows) > 1:
        sec("Tổng quan các kịch bản đã chọn")
        key = f"ovall_{p.id}_{'|'.join(row_key(r) for r in rows)}"
        if key not in st.session_state:
            with st.spinner("Đang vẽ biểu đồ tổng quan..."):
                h = hashlib.md5("|".join(sorted(row_key(r) for r in rows)).encode()).hexdigest()[:10]
                st.session_state[key] = build_overview(rows, sub_dir(p.id, f"charts/overview_{h}"))
        ov = st.session_state[key]
        for k in ("vus", "p95", "modules"):
            if ov.get(k) and (k != "modules" or len({r.get("module") for r in rows}) > 1):
                st.image(ov[k], caption=OVERVIEW_TITLES[k], width="stretch")
        cols = st.columns(2)
        for c, k in zip(cols, ("comparison", "throughput")):
            if ov.get(k):
                c.image(ov[k], caption=OVERVIEW_TITLES[k], width="stretch")
    sec("Chi tiết từng kịch bản")
    opts = {f"{r['run_id']}|{r['uc_code']}": f"{r['uc_code']} – {r['uc_name']} · {r['tool']} · {scenario_name(r['scenario_type'])}"
            for r in rows}
    sel = st.selectbox("Kịch bản", list(opts), format_func=opts.get)
    charts = per_row.get(sel, {})
    keys = [k for k in CHART_TITLES if k in charts and k != "resources"]
    for i in range(0, len(keys), 2):
        cols = st.columns(2)
        for c, k in zip(cols, keys[i:i + 2]):
            c.image(charts[k], caption=CHART_TITLES[k], width="stretch")
    if charts.get("resources"):
        st.image(charts["resources"], caption=CHART_TITLES["resources"], width="stretch")
    st.caption(f"Ảnh PNG lưu tại: {sub_dir(p.id, 'charts')}")
    state.nav_buttons("results", "report")
