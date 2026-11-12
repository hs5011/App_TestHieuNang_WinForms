"""Bước 8 - Thu thập kết quả & phân tích."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ...charts.charts import run_time
from ...models import Project
from ...results.analyzer import analyze_all, flatten
from ...results.insights import module_summaries
from ...results.metrics import fmt_ms, fmt_pct
from ...scriptgen.profile import scenario_name
from .. import state
from ..components import section_titles
from .s07_run import sync_runs


def get_rows(p: Project, force: bool = False) -> list[dict]:
    key = f"rows_{p.id}_{'|'.join(r.run_id for r in p.runs if r.status == 'done')}"
    if force or key not in st.session_state:
        with st.spinner("Đang đọc & phân tích kết quả..."):
            results = analyze_all(p, force=force)
        st.session_state[key] = (results, flatten(results))
    return st.session_state[key]


def row_key(r: dict) -> str:
    return f"{r['run_id']}|{r['uc_code']}"


def selected_rows(p: Project, rows: list[dict]) -> list[dict]:
    """Các kịch bản đã chọn ở bước 8 (lưu dạng danh sách loại trừ để lượt chạy mới tự được đưa vào)."""
    excl = set(p.excluded_rows)
    return [r for r in rows if row_key(r) not in excl]


def render(p: Project) -> None:
    st.header("8. Kết quả & phân tích")
    sync_runs(p)
    done = [r for r in p.runs if r.status == "done"]
    if not done:
        st.warning("Chưa có lượt chạy hoàn tất (bước 7).")
        state.nav_buttons("run", None)
        return
    results, all_rows = get_rows(p)
    for r in results:
        if r.get("error"):
            st.error(f"Lượt {r['run_id']}: {r['error']}")
    if not all_rows:
        st.warning("Không có dữ liệu để phân tích.")
        return

    sec = section_titles(8)
    sec("Chọn kịch bản đưa vào thống kê & báo cáo")
    opts = {row_key(r): f"{r['uc_code']} · {r['tool']} · {scenario_name(r['scenario_type'])} · {r['run_id'][:15]}"
            for r in all_rows}
    applied = [row_key(r) for r in selected_rows(p, all_rows)]
    chosen = st.multiselect("Kịch bản đưa vào thống kê & báo cáo", list(opts), default=applied, format_func=opts.get,
                            key=f"sel_rows_{p.id}",
                            help="Chọn kịch bản rồi bấm Phân tích lại: toàn bộ thống kê bên dưới và báo cáo ở bước 10 "
                                 "sẽ chỉ tính trên các kịch bản này. Lượt chạy mới sẽ tự được đưa vào.")
    b1, b2 = st.columns([1, 3], vertical_alignment="center")
    if b1.button("🔄 Phân tích lại", disabled=not chosen, width="stretch"):
        if set(chosen) == set(applied):
            get_rows(p, force=True)   # không đổi lựa chọn -> đọc & phân tích lại file kết quả
        else:
            p.excluded_rows = [k for k in opts if k not in chosen]   # đổi lựa chọn -> chỉ thống kê lại, dùng kết quả đã phân tích
            state.save(p)
        st.rerun()
    if not chosen:
        b2.warning("Cần chọn ít nhất 1 kịch bản.")
    elif set(chosen) != set(applied):
        b2.info("Lựa chọn đã thay đổi – bấm **Phân tích lại** để cập nhật thống kê.")
    else:
        b2.caption(f"Đang thống kê {len(applied)}/{len(opts)} kịch bản.")
    rows = selected_rows(p, all_rows)
    if not rows:
        st.warning("Chưa có kịch bản nào được chọn.")
        return

    passed = sum(1 for r in rows if r["evaluation"]["passed"])
    sec("Tổng quan kết quả")
    m = st.columns(4)
    m[0].metric("Kịch bản đã chạy", len(rows))
    m[1].metric("Đạt", passed)
    m[2].metric("Không đạt", len(rows) - passed)
    m[3].metric("Tổng request", f"{sum(r['summary'].get('samples', 0) for r in rows):,}".replace(",", "."))

    mods = module_summaries(rows)
    if len(mods) > 1:
        sec("Tổng hợp theo phân hệ")
        icon = {"Đạt": "✅", "Đạt một phần": "⚠️", "Không đạt": "❌"}
        st.dataframe(pd.DataFrame([{
            "Kết luận": f"{icon[m['verdict']]} {m['verdict']}", "Phân hệ": m["module"], "Số UC": len(m["ucs"]),
            "UC": ", ".join(m["ucs"]), "Kịch bản đạt": f"{m['passed']}/{m['runs']}",
            "p95 cao nhất (ms)": round(m["p95_max"]), "UC có p95 cao nhất": m["p95_worst_uc"],
            "Tỷ lệ lỗi": fmt_pct(m["error_rate"]), "Thông lượng TB (req/s)": round(m["throughput"], 2)} for m in mods]),
            width="stretch", hide_index=True)
        with st.expander("Nhận định theo phân hệ"):
            st.markdown("\n".join(f"- {m['text']}" for m in mods))

    sec("Bảng tổng hợp")
    st.dataframe(pd.DataFrame([{
        "Kết luận": "✅ Đạt" if r["evaluation"]["passed"] else "❌ Không đạt",
        "Phân hệ": r.get("module") or "—", "Mã UC": r["uc_code"],
        "Tên UC": r["uc_name"], "Công cụ": r["tool"], "Kịch bản": scenario_name(r["scenario_type"]),
        "VUs tối đa": r["summary"].get("max_vus"), "Request": r["summary"].get("samples"),
        "Throughput (req/s)": round(r["summary"].get("throughput", 0), 2),
        "TB (ms)": round(r["summary"].get("avg") or 0), "Min": round(r["summary"].get("min") or 0),
        "Max": round(r["summary"].get("max") or 0), "p90": round(r["summary"].get("p90") or 0),
        "p95": round(r["summary"].get("p95") or 0), "p99": round(r["summary"].get("p99") or 0),
        "Ngưỡng p95": r["p95_threshold"], "Tỷ lệ lỗi": fmt_pct(r["summary"].get("error_rate")),
    } for r in rows]), width="stretch", hide_index=True)

    sec("Chi tiết theo UC / API" + (" – nhóm theo phân hệ" if len(mods) > 1 else ""))
    last_mod = None
    for r in sorted(rows, key=lambda r: [m["module"] for m in mods].index(r.get("module") or "Chưa phân loại")):
        s, ev = r["summary"], r["evaluation"]
        mod = r.get("module") or "Chưa phân loại"
        if len(mods) > 1 and mod != last_mod:
            ms = next(m for m in mods if m["module"] == mod)
            st.markdown(f"##### 📁 {mod} – {ms['verdict']} ({ms['passed']}/{ms['runs']} kịch bản đạt)")
            last_mod = mod
        with st.expander(f"{'✅' if ev['passed'] else '❌'} {r['uc_code']} – {r['uc_name']} · {r['tool']} · "
                         f"{scenario_name(r['scenario_type'])} · chạy lúc {run_time(r)} · p95 {fmt_ms(s.get('p95'))}"):
            c = st.columns(6)
            c[0].metric("Request", s.get("samples"))
            c[1].metric("Throughput", f"{s.get('throughput', 0):.2f}/s")
            c[2].metric("Trung bình", fmt_ms(s.get("avg")))
            c[3].metric("p95", fmt_ms(s.get("p95")), delta=f"ngưỡng {r['p95_threshold']:.0f}", delta_color="off")
            c[4].metric("p99", fmt_ms(s.get("p99")))
            c[5].metric("Tỷ lệ lỗi", fmt_pct(s.get("error_rate")))
            (st.success if ev["passed"] else st.error)(r["verdict"])
            st.markdown("**Nhận xét tự động:**\n" + "\n".join(f"- {x}" for x in r["insights"]))
            if r["labels"]:
                st.dataframe(pd.DataFrame(r["labels"]).drop(columns=["uc"]).round(1), width="stretch",
                             hide_index=True)

    from .monitor_panel import render_data
    render_data(p, rows, sec)

    sec("Nhận xét bổ sung cho báo cáo")
    txt = st.text_area("Mỗi dòng là một ý (sẽ đưa vào mục Tổng hợp & kết luận)", p.conclusions, height=120)
    if st.button("💾 Lưu nhận xét"):
        p.conclusions = txt
        state.save(p)
        st.success("Đã lưu.")

    state.nav_buttons("run", "charts")
