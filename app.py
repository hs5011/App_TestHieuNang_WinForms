"""PerfTool - Ứng dụng phân tích Use Case & kiểm thử hiệu năng Web / WinForms (chạy local).

Chạy:  streamlit run app.py   (hoặc run.bat)
"""
from __future__ import annotations

import streamlit as st

from perftool import __version__
from perftool.storage import BROKEN_PROJECTS, create_project, delete_project, list_projects
from perftool.ui import state
from perftool.ui.steps import (s01_info, s02_import, s03_crawl, s04_score, s05_confirm, s06_tool, s07_run,
                               s08_results, s09_charts, s10_report)

st.set_page_config(page_title="PerfTool – Kiểm thử hiệu năng", page_icon="⚡", layout="wide")

PAGES = {
    "info": s01_info.render, "import": s02_import.render, "crawl": s03_crawl.render, "score": s04_score.render,
    "confirm": s05_confirm.render, "tool": s06_tool.render, "run": s07_run.render, "results": s08_results.render,
    "charts": s09_charts.render, "report": s10_report.render,
}


def sidebar() -> None:
    with st.sidebar:
        st.markdown(f"## ⚡ PerfTool <small>v{__version__}</small>", unsafe_allow_html=True)
        projects = list_projects()
        if BROKEN_PROJECTS:
            st.warning("Không nạp được dự án (file project.json hỏng): "
                       + "; ".join(f"`{k}` – {v}" for k, v in BROKEN_PROJECTS.items()))
        ids = [p.id for p in projects]
        labels = {p.id: f"{p.info.name}  ·  {p.updated_at[:10]}" for p in projects}
        cur = st.session_state.get("project_id")
        if ids:
            idx = ids.index(cur) if cur in ids else 0
            sel = st.selectbox("Dự án", ids, index=idx, format_func=lambda i: labels[i])
            if sel != cur:
                st.session_state["project_id"] = sel
                st.session_state.pop("_project", None)
                st.session_state["step"] = "info"
                st.rerun()
        with st.expander("➕ Tạo dự án mới", expanded=not ids):
            # form xoá ô tên sau khi tạo -> không giữ tên cũ (dễ tạo trùng dự án)
            with st.form("new_project_form", clear_on_submit=True, border=False):
                name = st.text_input("Tên dự án", key="new_project_name")
                if st.form_submit_button("Tạo dự án", type="primary"):
                    if not name.strip():
                        st.warning("Nhập tên dự án.")
                    else:
                        p = create_project(name.strip())
                        st.session_state["project_id"] = p.id
                        st.session_state.pop("_project", None)
                        st.session_state["step"] = "info"
                        st.rerun()

        p = state.project()
        if not p:
            return
        st.divider()
        st.caption("CÁC BƯỚC THỰC HIỆN")
        cur_step = st.session_state.get("step", "info")
        for key, label in state.STEPS:
            if key == "crawl" and p.kind == "winforms":
                label = "3. Ghi API ứng dụng WinForms"
            icon = "✅" if state.step_done(p, key) else "⬜"
            if st.button(f"{icon} {label}", key=f"nav_{key}", width="stretch",
                         type="primary" if key == cur_step else "secondary"):
                state.goto(key)
        st.divider()
        with st.expander("⚠️ Xoá dự án"):
            if st.button("Xoá vĩnh viễn dự án này", type="secondary"):
                delete_project(p.id)
                st.session_state.pop("project_id", None)
                st.session_state.pop("_project", None)
                st.rerun()


def main() -> None:
    sidebar()
    p = state.project()
    if not p:
        st.title("⚡ PerfTool")
        st.info("Tạo hoặc chọn một dự án ở thanh bên trái để bắt đầu.")
        st.markdown(
            "**Quy trình:** Nhập thông tin → Import UC → Đăng nhập & phân tích Web (hoặc ghi API WinForms) → Phân tích và chọn UC → "
            "Xác nhận → Chọn k6/JMeter → Cấu hình & chạy test → Thu thập kết quả → Phân tích → Biểu đồ → Xuất báo cáo")
        return
    step = st.session_state.get("step", "info")
    PAGES.get(step, s01_info.render)(p)


main()
