"""Bước 2 - Import danh sách Use Case từ Excel/CSV."""
from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

from ...config import ROOT_DIR
from ...models import Project
from ...storage import sub_dir
from ...uc_import import importer
from .. import state
from ..components import section_titles


def render(p: Project) -> None:
    st.header("2. Import danh sách Use Case")
    st.caption("Hỗ trợ .xlsx/.xls/.csv. Ứng dụng tự tìm dòng tiêu đề và nhận diện cột; bạn có thể chỉnh lại ánh xạ.")

    sec = section_titles(2)
    sec("Chọn file danh sách UC")
    c1, c2 = st.columns([3, 1])
    up = c1.file_uploader("Chọn file danh sách UC", type=["xlsx", "xls", "csv", "txt"])
    sample = ROOT_DIR / "samples" / "uc_mau.xlsx"
    if sample.exists():
        c2.download_button("⬇ Tải file UC mẫu", sample.read_bytes(), file_name="uc_mau.xlsx", width="stretch")

    if up is not None:
        data = up.getvalue()
        sheet = 0
        if not up.name.lower().endswith((".csv", ".txt")):
            sheets = importer.excel_sheets(io.BytesIO(data))
            if len(sheets) > 1:
                sheet = st.selectbox("Sheet", sheets)
        try:
            df = importer.read_table(io.BytesIO(data), up.name, sheet)
        except Exception as e:  # noqa: BLE001
            st.error(f"Không đọc được file: {e}")
            return
        st.write(f"Đọc được **{len(df)}** dòng, **{len(df.columns)}** cột.")
        guess = importer.guess_mapping(list(df.columns))
        sec("Ánh xạ cột")
        cols = ["(không dùng)"] + list(df.columns)
        mapping = {}
        grid = st.columns(5)
        for i, f in enumerate(importer.FIELDS):
            default = guess.get(f)
            label = importer.FIELD_LABELS[f] + (" *" if f == "name" else "")
            v = grid[i % 5].selectbox(label, cols, index=cols.index(default) if default in cols else 0, key=f"map_{f}")
            if v != "(không dùng)":
                mapping[f] = v
        ffill = st.checkbox("Tự điền Phân hệ cho các dòng trống (ô gộp trong Excel)", True)
        with st.expander("Xem dữ liệu gốc", expanded=False):
            st.dataframe(df.head(50), width="stretch")
        if st.button("📥 Nhập danh sách UC", type="primary", disabled="name" not in mapping):
            ucs = importer.to_use_cases(df, mapping, ffill)
            (sub_dir(p.id, "input") / up.name).write_bytes(data)
            p.use_cases, p.uc_source_file = ucs, up.name
            p.scores, p.confirmed, p.scenarios = [], False, []
            state.save(p)
            st.success(f"Đã nhập {len(ucs)} use case.")
            st.rerun()

    if p.use_cases:
        sec(f"Danh sách UC hiện tại ({len(p.use_cases)}) – có thể chỉnh sửa trực tiếp")
        if p.uc_source_file:
            st.caption(f"Nguồn: {p.uc_source_file}")
        dup = [u.code for u in p.use_cases if re.search(r" \(\d+\)$", u.code)]
        if dup:
            st.error(f"Có {len(dup)} mã UC bị trùng, đã tự đổi thành: {', '.join(dup[:10])}"
                     + (" …" if len(dup) > 10 else "") + ". Nên sửa lại mã cho đúng trong bảng dưới rồi bấm Lưu.")
        df = importer.use_cases_to_df(p.use_cases)
        edited = st.data_editor(df, num_rows="dynamic", width="stretch", height=420, key=f"uc_editor_{p.id}",
                                column_config={"Số bước": st.column_config.NumberColumn(min_value=0, step=1),
                                               "Mô tả": st.column_config.TextColumn(width="large")})
        c1, c2 = st.columns([1, 4])
        if c1.button("💾 Lưu thay đổi"):
            p.use_cases = importer.df_to_use_cases(edited, p.use_cases)
            state.save(p)
            st.success("Đã lưu.")
        mods = pd.Series([u.module or "(trống)" for u in p.use_cases]).value_counts()
        c2.caption("Phân hệ: " + " · ".join(f"{k} ({v})" for k, v in mods.items()))

    state.nav_buttons("info", "crawl", next_disabled=not p.use_cases)
