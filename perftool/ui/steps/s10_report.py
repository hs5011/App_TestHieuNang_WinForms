"""Bước 10 - Xuất báo cáo."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from ...charts.builder import OVERVIEW_TITLES, build_overview
from ...models import Project
from ...report.docx_report import build_report, export_excel, try_export_pdf
from ...results.insights import overview_insights
from ...scriptgen.profile import scenario_name
from ...storage import slugify, sub_dir
from .. import state
from .s08_results import get_rows, selected_rows
from .s09_charts import get_charts


def _overview(p: Project, sel_rows: list[dict]) -> dict[str, str]:
    """Biểu đồ tổng quan cho đúng tập kịch bản đang chọn (cache theo lựa chọn)."""
    ids = sorted(f"{r['run_id']}|{r['uc_code']}" for r in sel_rows)
    key = "ov_" + hashlib.md5("|".join(ids).encode()).hexdigest()[:10]
    if key not in st.session_state:
        with st.spinner("Đang vẽ biểu đồ tổng quan..."):
            st.session_state[key] = build_overview(sel_rows, sub_dir(p.id, f"charts/overview_{key[3:]}"))
    return st.session_state[key]


def render(p: Project) -> None:
    st.header("10. Xuất báo cáo Performance Test")
    if not any(r.status == "done" for r in p.runs):
        st.warning("Chưa có kết quả.")
        state.nav_buttons("charts", None)
        return
    _, rows = get_rows(p)
    if not rows:
        st.warning("Không có dữ liệu.")
        return
    sel_rows = selected_rows(p, rows)
    st.subheader("10a. Kịch bản đưa vào báo cáo")
    c_info, c_btn = st.columns([3, 1], vertical_alignment="center")
    c_info.info(f"Báo cáo gồm **{len(sel_rows)}/{len(rows)} kịch bản** đã chọn ở Bước 8: "
                + ", ".join(sorted({r['uc_code'] for r in sel_rows})))
    if c_btn.button("✏️ Đổi lựa chọn", width="stretch"):
        state.goto("results")
    multi = len(sel_rows) > 1
    n_mod = len({r.get("module") or "Chưa phân loại" for r in sel_rows})
    st.subheader("10b. Nội dung báo cáo")
    c0, c1, c2 = st.columns(3)
    c3, c4, c6 = st.columns(3)
    c5, _, _ = st.columns(3)
    ov = c0.checkbox("Mục tổng quan tất cả kịch bản", multi, disabled=not multi,
                     help="Bảng tổng hợp theo loại kịch bản × công cụ, biểu đồ VUs/p95/thông lượng của tất cả kịch bản "
                          "đã chọn và nhận xét so sánh. Cần chọn từ 2 kịch bản trở lên.")
    by_mod = c1.checkbox("Mục tổng hợp theo phân hệ", n_mod > 1, disabled=n_mod < 2,
                         help="Bảng kết quả theo từng phân hệ (đạt/tổng, p95 cao nhất, tỷ lệ lỗi…), biểu đồ p95 theo phân hệ "
                              "và nhận định từng phân hệ; bảng tổng hợp kịch bản cũng được nhóm theo phân hệ. "
                              f"Cần kịch bản thuộc từ 2 phân hệ trở lên (hiện chọn {n_mod} phân hệ).")
    extra = c2.checkbox("Biểu đồ bổ sung", True,
                        help="Thêm biểu đồ thông lượng, số người dùng đồng thời (VUs), tỷ lệ lỗi và p95 theo API cho từng kịch bản.")
    api_tbl = c3.checkbox("Bảng chi tiết theo request/API", True)
    ins = c4.checkbox("Nhận xét chi tiết tự động", True)
    n_res = _resource_runs(p, sel_rows)
    res = c6.checkbox("Tài nguyên máy chủ", n_res > 0, disabled=n_res == 0,
                      help="Bảng CPU/RAM/ổ đĩa/mạng/kết nối CSDL (trung bình, cao nhất), biểu đồ và nhận xét cho từng "
                           f"kịch bản có số liệu. Hiện có {n_res}/{len(sel_rows)} kịch bản có số liệu "
                           "(bật thu thập ở Bước 6 – tuỳ chọn).")
    pdf = c5.checkbox("Xuất thêm PDF", False, help="Chuyển file Word sang PDF – cần cài Microsoft Word trên máy.")
    st.caption("Định dạng theo mẫu `Template_BaoCaoHieuNang.docx` (đổi mẫu trong config/settings.yaml → report.template).")

    if multi and ov:
        with st.expander("👁 Xem trước mục tổng quan", expanded=False):
            charts_ov = _overview(p, sel_rows)
            for key in ("vus", "p95", "modules", "comparison", "throughput"):
                if charts_ov.get(key):
                    st.image(charts_ov[key], caption=OVERVIEW_TITLES[key], width="stretch")
            st.markdown("**Nhận xét tổng quan:**\n" + "\n".join(f"- {n}" for n in overview_insights(sel_rows)))

    st.subheader("10c. Tạo & tải báo cáo")
    if st.button("📄 Tạo báo cáo", type="primary", disabled=not sel_rows):
        per_row, cmp = get_charts(p, sel_rows)
        charts_ov = _overview(p, sel_rows) if multi else {}
        out_dir = sub_dir(p.id, "reports")
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        base = out_dir / f"BaoCao_HieuNang_{slugify(p.info.name)}_{stamp}"
        with st.spinner("Đang tạo báo cáo..."):
            docx = build_report(p, sel_rows, per_row, charts_ov.get("comparison", cmp), base.with_suffix(".docx"),
                                {"extra_charts": extra, "api_table": api_tbl, "insights": ins,
                                 "overview": ov and multi, "overview_charts": charts_ov,
                                 "modules": by_mod and n_mod > 1, "resources": res})
            mon = None
            if res:
                from ...monitor.store import load_all
                mon = load_all(p.id)
            xlsx = export_excel(sel_rows, base.with_suffix(".xlsx"), mon)
            pdf_file = None
            if pdf:
                try:
                    pdf_file = try_export_pdf(docx)
                except Exception as e:  # noqa: BLE001
                    st.warning(f"Không xuất được PDF: {e}")
        st.session_state["last_report"] = [str(docx), str(xlsx)] + ([str(pdf_file)] if pdf_file else [])
        st.success("Đã tạo báo cáo.")

    files = [Path(f) for f in st.session_state.get("last_report", []) if Path(f).exists()]
    if files:
        cols = st.columns(len(files) + 1)
        mime = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pdf": "application/pdf"}
        for c, f in zip(cols, files):
            c.download_button(f"⬇ {f.suffix[1:].upper()}", f.read_bytes(), file_name=f.name,
                              mime=mime.get(f.suffix), width="stretch")
        if os.name == "nt" and cols[-1].button("📂 Mở thư mục", width="stretch"):
            os.startfile(files[0].parent)  # noqa: S606
        st.caption(f"Lưu tại: {files[0].parent}")

    old = sorted(sub_dir(p.id, "reports").glob("*.docx"), reverse=True)
    if old:
        with st.expander(f"Báo cáo đã tạo ({len(old)})"):
            for f in old[:20]:
                st.download_button(f.name, f.read_bytes(), file_name=f.name, key=f"old_{f.name}")
    state.nav_buttons("charts", None)


def _resource_runs(p: Project, sel_rows: list[dict]) -> int:
    """Số kịch bản đã chọn có số liệu tài nguyên máy chủ trong khung giờ chạy."""
    if not p.monitor.enabled:
        return 0
    from ...monitor.store import load_all, run_slice
    mon = load_all(p.id)
    if mon.empty:
        return 0
    return sum(1 for r in sel_rows if not run_slice(mon, r.get("started_at", ""), r.get("finished_at", "")).empty)
