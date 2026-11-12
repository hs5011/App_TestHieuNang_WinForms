"""Bước 5 - Xem, chỉnh sửa kịch bản từng UC và xác nhận trước khi test."""
from __future__ import annotations

from datetime import datetime

import json
import math
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from ...analysis.complexity import build_scenario
from ...crawler.guard import classify, is_query_request, page_has_write
from ...models import Project, ScenarioStep, TestScenario
from .. import state
from ..components import check_all_buttons

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _steps_df(sc: TestScenario) -> pd.DataFrame:
    cols = ["Bật", "Tên bước", "Method", "URL", "Content-Type", "Body", "Headers (JSON)", "Think time (s)",
            "Trích token", "Biến token", "Dùng token"]
    return pd.DataFrame([{"Bật": s.enabled, "Tên bước": s.name, "Method": s.method, "URL": s.url,
                          "Content-Type": s.content_type, "Body": s.body,
                          "Headers (JSON)": json.dumps(s.headers, ensure_ascii=False) if s.headers else "",
                          "Think time (s)": s.think_time_s, "Trích token": s.extract_token,
                          "Biến token": s.token_var, "Dùng token": s.use_token}
                         for s in sc.steps], columns=cols)


def _num(v) -> float:
    """Ô số bị xoá trống trên bảng -> NaN/None; NaN làm hỏng script và project.json -> 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f >= 0 else 0.0


def _flag(v) -> bool:
    """Ô checkbox của dòng mới thêm là None/NaN (bool(NaN) = True) -> coi là tắt."""
    return bool(v) and not (isinstance(v, float) and math.isnan(v))


def _df_steps(df: pd.DataFrame) -> list[ScenarioStep]:
    out = []
    for _, r in df.iterrows():
        url = str(r.get("URL") or "").strip()
        if not url or url == "nan":
            continue

        def s(k: str) -> str:
            v = r.get(k)
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

        try:
            headers = json.loads(s("Headers (JSON)")) if s("Headers (JSON)").strip() else {}
        except json.JSONDecodeError:
            headers = {}
        out.append(ScenarioStep(name=s("Tên bước") or url[-60:], method=(s("Method") or "GET").upper(), url=url,
                                content_type=s("Content-Type"), body=s("Body"), headers=headers,
                                think_time_s=_num(r.get("Think time (s)")), enabled=_flag(r.get("Bật")),
                                extract_token=s("Trích token"), token_var=s("Biến token"), use_token=s("Dùng token")))
    return out


def render(p: Project) -> None:
    st.header("5. Xác nhận phạm vi & kịch bản kiểm thử")
    selected = p.selected_scores()
    if not selected:
        st.warning("Chưa chọn UC nào ở bước 4.")
        state.nav_buttons("score", None)
        return

    # đồng bộ danh sách kịch bản với UC đã chọn
    existing = {sc.uc_code: sc for sc in p.scenarios}
    scenarios = [existing.get(s.uc_code) or build_scenario(p, s) for s in selected]
    if [sc.uc_code for sc in scenarios] != [sc.uc_code for sc in p.scenarios]:
        p.scenarios = scenarios
        p.confirmed = False
        state.save(p)

    if p.confirmed:
        st.success(f"✅ Đã xác nhận lúc {p.confirmed_at}. Chỉnh sửa bất kỳ sẽ yêu cầu xác nhận lại.")
    else:
        st.info("Kiểm tra danh sách request của từng UC. Request ghi dữ liệu (POST/PUT/DELETE) mặc định bị tắt để "
                "tránh tạo dữ liệu rác – bật lại nếu muốn kiểm thử thao tác ghi.")

    st.subheader("5a. Danh sách request của từng UC")
    for i, sc in enumerate(p.scenarios):
        score = next((s for s in selected if s.uc_code == sc.uc_code), None)
        with st.expander(f"**{sc.uc_code} – {sc.uc_name}**  ·  {sum(s.enabled for s in sc.steps)}/{len(sc.steps)} "
                         f"request bật · điểm {score.total if score else 0:.1f}", expanded=i == 0):
            if score:
                st.caption(f"Lý do đề xuất: {score.reason}")
            uc = p.uc(sc.uc_code)
            if uc and uc.description:
                with st.popover("📄 Mô tả UC"):
                    st.write(uc.description)
            c1, c2 = st.columns([1, 3])
            thr = c1.number_input("Ngưỡng p95 riêng (ms, 0 = dùng mặc định)", 0, 600000,
                                  int(sc.p95_threshold_ms or 0), 100, key=f"thr_{p.id}_{i}_{sc.uc_code}")
            val = check_all_buttons(f"step_chk_{p.id}_{i}_{sc.uc_code}", len(sc.steps), "☑ Bật tất cả", "☐ Tắt tất cả",
                                    help="Bật/tắt mọi request của UC này (lưu ngay). Lưu ý: Bật tất cả sẽ bật cả request "
                                         "ghi dữ liệu POST/PUT/DELETE.")
            if val is not None:
                for s_ in sc.steps:
                    s_.enabled = val
                p.confirmed = False
                state.save(p)
                st.session_state.pop(f"steps_{p.id}_{i}_{sc.uc_code}", None)
                st.rerun()
            writes = [s_.name for s_ in sc.steps if s_.enabled and not s_.extract_token
                      and not (is_query_request(s_.method, s_.url) and not page_has_write(s_.url))]
            review = [s_ for s_ in sc.steps if not s_.enabled and classify(s_.method, s_.url) == "unknown"]
            if review:
                st.info(f"🔎 {len(review)} request **chưa rõ là đọc hay ghi** nên đang TẮT (GET trỏ tới 1 bản ghi mà tên không "
                        "phải truy vấn – có thể là 'đánh dấu đã xem', 'ghim'…). Kiểm tra rồi bật nếu chắc chắn chỉ đọc: "
                        + ", ".join(f"`{s_.method} {urlparse(s_.url).path}`" for s_ in review[:8])
                        + (" …" if len(review) > 8 else ""))
            if writes:
                st.warning(f"Đang bật {len(writes)} request có thể ghi dữ liệu (POST/PUT/PATCH/DELETE, hoặc GET có từ ghi "
                           "trong URL như markread, delete, xu-ly…) – chạy test sẽ lặp lại chúng trên hệ thống thật: "
                           + ", ".join(writes[:8]) + (" …" if len(writes) > 8 else "") + ". Tắt các dòng này nếu không muốn.")
            ed = st.data_editor(_steps_df(sc), num_rows="dynamic", width="stretch", key=f"steps_{p.id}_{i}_{sc.uc_code}",
                                column_config={
                                    "Method": st.column_config.SelectboxColumn(options=METHODS),
                                    "Content-Type": st.column_config.SelectboxColumn(
                                        options=["", "application/json", "application/x-www-form-urlencoded",
                                                 "text/plain", "multipart/form-data"]),
                                    "Body": st.column_config.TextColumn(width="medium"),
                                    "URL": st.column_config.TextColumn(width="large"),
                                    "Think time (s)": st.column_config.NumberColumn(min_value=0.0, step=0.5)})
            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button("💾 Lưu kịch bản", key=f"save_{p.id}_{i}_{sc.uc_code}"):
                sc.steps = _df_steps(ed)
                sc.p95_threshold_ms = float(thr) or None
                p.confirmed = False
                state.save(p)
                st.rerun()
            if b2.button("↺ Tạo lại từ dữ liệu crawl", key=f"regen_{p.id}_{i}_{sc.uc_code}") and score:
                new = build_scenario(p, score)
                sc.steps = new.steps
                p.confirmed = False
                state.save(p)
                st.rerun()

    st.divider()
    st.subheader("5b. Xác nhận phạm vi kiểm thử")
    empty = [sc.uc_code for sc in p.scenarios if not any(s.enabled for s in sc.steps)]
    if empty:
        st.error(f"Các UC chưa có request nào được bật: {', '.join(empty)}")
    c1, c2 = st.columns([1, 3])
    if c1.button("✅ Xác nhận phạm vi kiểm thử", type="primary", disabled=bool(empty) or p.confirmed):
        p.confirmed = True
        p.confirmed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state.save(p)
        st.rerun()
    if p.confirmed and c2.button("Huỷ xác nhận"):
        p.confirmed = False
        state.save(p)
        st.rerun()

    state.nav_buttons("score", "tool", next_disabled=not p.confirmed)
