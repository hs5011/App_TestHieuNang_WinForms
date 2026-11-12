"""Bước 4 - Chấm điểm độ phức tạp & đề xuất UC kiểm thử hiệu năng."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ... import config
from ...analysis import complexity as cx
from ...models import Project, UCScore
from .. import state
from ..components import check_all_buttons

CAPS_MODES = {"auto": "Tự động theo dữ liệu dự án (khuyến nghị)", "fixed": "Cố định (config/settings.yaml)"}
HOW = {
    "steps": "Cột *Số bước* trong file UC → nếu không có thì đếm số **giao dịch con** (mẫu QĐ 671) → nếu vẫn không có "
             "thì ước lượng từ mô tả.",
    "screens": "Số trang ghép được với UC ở bước 3 (tối đa 3 trang/UC) + số popup có biểu mẫu/bảng/nội dung chi tiết "
               "trên các trang đó (khi bật quét popup ở bước 3).",
    "api_count": "Số API (XHR/fetch) **riêng** của các màn hình đó – đã bỏ API khung dùng chung cho mọi trang "
                 "(session, menu, thông báo…).",
    "crud": "Số **loại** thao tác ghi xuất hiện trong tên/mô tả UC và nhãn nút trên màn hình (thêm mới, sửa, xoá, "
            "duyệt/ký, gửi/chuyển/giao, lưu/tải lên) + tối đa 2 điểm cho API ghi dữ liệu (POST/PUT/DELETE).",
    "data_processing": "Số **loại** thao tác xử lý dữ liệu (tìm kiếm/lọc, thống kê/báo cáo, xuất/nhập file, danh "
                       "sách/phân trang, theo dõi/cảnh báo) + 1 nếu có bảng dữ liệu + 1 nếu bảng ≥ 50 dòng + 1 nếu có "
                       "phản hồi > 100 KB.",
    "response_time": "~p90 thời gian phản hồi các request đo được khi quét ở bước 3 (chỉ 1 lần đo – tham khảo).",
}


def _fmt(k: str, v: float) -> str:
    return f"{v:.0f} {cx.CRITERIA_UNIT[k]}" if k == "response_time" else f"{v:g} {cx.CRITERIA_UNIT[k]}"


def _caps_text(caps: dict[str, float]) -> str:
    return " · ".join(f"{cx.CRITERIA[k]} {_fmt(k, v)}" for k, v in caps.items() if k in cx.CRITERIA)


def score_guide(weights: dict[str, float], caps: dict[str, float]) -> None:
    """Nội dung nút ❔ Giải thích."""
    wsum = sum(weights.values()) or 1
    st.markdown("""
#### Điểm độ phức tạp được tính thế nào?
1. **Đo 6 tiêu chí** cho từng UC (số liệu thô).
2. **Quy về thang 0–1**: chia số liệu thô cho **mức trần** của tiêu chí; vượt mức trần tính 1.
3. **Cộng có trọng số**: `Điểm = 100 × Σ(điểm tiêu chí × trọng số) ÷ Σ(trọng số)`.

**Trọng số = mức độ quan trọng** của từng tiêu chí. Tổng trọng số không cần bằng 1 (tự chia lại). Kéo về 0 = bỏ
tiêu chí đó; kéo tất cả bằng nhau = 6 tiêu chí ngang nhau. Giá trị mặc định là quy ước của ứng dụng (ưu tiên API,
xử lý dữ liệu, thời gian phản hồi – những thứ gây tải lên máy chủ/CSDL), không theo chuẩn bắt buộc nào.
""")
    st.dataframe(pd.DataFrame([{
        "Tiêu chí": label, "Cách đo": HOW[k].replace("**", "").replace("*", ""),   # bảng không hiển thị Markdown
        "Mức trần": _fmt(k, caps[k]) if caps else "—",
        "Trọng số": f"{weights.get(k, 0):.2f} ({weights.get(k, 0) / wsum:.0%})"} for k, label in cx.CRITERIA.items()]),
        hide_index=True, width="stretch",
        column_config={"Cách đo": st.column_config.TextColumn(width="large")})
    st.markdown("""
**Mức trần tự động** = p90 của chính danh sách UC trong dự án (không nhỏ hơn một mức sàn) → khoảng 10% UC đạt tối đa ở
mỗi tiêu chí, còn lại trải đều 0–1, nên tiêu chí nào cũng giúp phân biệt UC. **Mức trần cố định** lấy trong
`config/settings.yaml` (mục `scoring.caps`) – dùng khi muốn so sánh điểm giữa các dự án khác nhau.

**Khi hai UC bằng điểm:** UC có mức độ phức tạp khai báo trong file UC cao hơn (Phức tạp > Trung bình > Đơn giản) xếp
trên, rồi đến UC nhiều bước hơn. **Không đề xuất** hai UC dùng gần như cùng bộ API (trùng > 60%) để tránh test lặp.

| Mục tiêu | Gợi ý chỉnh trọng số |
|---|---|
| Tìm UC gây tải nặng nhất lên máy chủ/CSDL | Tăng **Số API** và **Xử lý dữ liệu** |
| Hệ thống đang có chỗ chậm rõ rệt | Tăng **Thời gian phản hồi** |
| File UC ghi số bước/giao dịch không đáng tin | Giảm **Số bước** |
| Nhiều UC không ghép được màn hình (hệ thống ngoài, thiếu quyền…) | Giảm **Số màn hình / Số API / Thời gian phản hồi** |
""")


def scores_df(p: Project, view: str, weights: dict[str, float]) -> pd.DataFrame:
    web = {pg.url: m.name for m in p.modules for pg in m.pages}   # URL màn hình -> tên phân hệ (menu) trên web
    rows = []
    for s in sorted(p.scores, key=lambda x: x.total, reverse=True):
        if view == "contrib":
            crit = {cx.CRITERIA[k]: v for k, v in cx.contributions(s.scores, weights).items()}
        elif view == "score":
            crit = {cx.CRITERIA[k]: s.scores.get(k, 0) for k in cx.CRITERIA}
        else:
            crit = {cx.CRITERIA[k]: s.raw.get(k, 0) for k in cx.CRITERIA}
        web_mods = sorted({web.get(u, "") for u in s.matched_pages} - {""})
        rows.append({"Chọn": s.selected, "Đề xuất": "⭐" if s.recommended else "", "Mã UC": s.uc_code,
                     "Tên UC": s.uc_name, "Tên phân hệ": s.module or "—",
                     "Phân hệ trên web": ", ".join(web_mods) or "—", "Điểm": s.total, **crit,
                     "Số màn hình ghép": len(s.matched_pages), "Độ tin cậy ghép": s.match_confidence, "Lý do": s.reason})
    return pd.DataFrame(rows)


def breakdown(p: Project, s: UCScore, weights: dict[str, float]) -> None:
    """Vì sao UC được điểm như vậy: bảng từng tiêu chí + biểu đồ đóng góp + thao tác nhận diện được."""
    caps = p.score_caps or {}
    contrib = cx.contributions(s.scores, weights)
    wsum = sum(weights.values()) or 1
    st.dataframe(pd.DataFrame([{
        "Tiêu chí": label, "Số liệu thô": _fmt(k, s.raw.get(k, 0)),
        "Mức trần": _fmt(k, caps[k]) if k in caps else "—", "Điểm 0–1": s.scores.get(k, 0),
        "Trọng số": round(weights.get(k, 0) / wsum, 3), "Góp vào tổng": contrib[k]} for k, label in cx.CRITERIA.items()]),
        hide_index=True, width="stretch",
        column_config={"Điểm 0–1": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
                       "Trọng số": st.column_config.NumberColumn(format="percent"),
                       "Góp vào tổng": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")})
    st.caption(f"Tổng: **{sum(contrib.values()):.1f}** điểm (đã lưu: {s.total:.1f}). "
               + ("Tiêu chí bị 0 thường do UC không ghép được màn hình ở bước 3." if not s.matched_pages else ""))
    uc = p.uc(s.uc_code)
    if uc:
        pages = [pg for pg in p.all_pages() if pg.url in s.matched_pages]
        btns = " ".join(b for pg in pages for b in pg.crud_buttons)
        text = f"{uc.name} {uc.description}"
        crud = sorted(set(cx.matched_groups(text, "crud")) | set(cx.matched_groups(btns, "crud")))
        data = cx.matched_groups(text, "data_processing")
        # điểm cộng ngoài số loại thao tác (cùng quy tắc compute_raw): số liệu thô = số loại + điểm cộng
        n_w = max(0, int(s.raw.get("crud", 0)) - len(crud))
        extra_d = [t for t, ok in (("có bảng dữ liệu", sum(pg.tables for pg in pages)),
                                   ("bảng ≥ 50 dòng", sum(pg.table_rows for pg in pages) >= 50),
                                   ("có phản hồi > 100 KB", any((r.response_size or 0) > 100_000
                                                                for pg in pages for r in pg.requests))) if ok]
        st.markdown(
            f"- **Loại thao tác ghi nhận diện được:** {', '.join(cx.GROUP_LABELS.get(g, g) for g in crud) or '—'}"
            + (f" · **+{n_w}** điểm cộng vì màn hình có API ghi dữ liệu (tối đa +2)" if n_w else "") + "\n"
            f"- **Loại thao tác xử lý dữ liệu:** {', '.join(cx.GROUP_LABELS.get(g, g) for g in data) or '—'}"
            + (f" · điểm cộng: {', '.join('+1 ' + t for t in extra_d)}" if extra_d else "") + "\n"
            f"- **Màn hình ghép:** {', '.join(pg.menu_text or pg.title or pg.url for pg in pages) or 'không có'}"
            + (f" (độ tin cậy {s.match_confidence:.0%})" if pages else "")
            + (("\n- **Popup được tính là màn hình:** " + ", ".join(
                pp.title or pp.trigger for pg in pages for pp in pg.popups if pp.counted))
               if any(pp.counted for pg in pages for pp in pg.popups) else "")
            + (f"\n- **Mức độ khai báo trong file UC:** {uc.complexity}" if uc.complexity else ""))


def render(p: Project) -> None:
    st.header("4. Phân tích độ phức tạp & chọn UC")
    if not p.use_cases:
        st.warning("Chưa có danh sách UC (bước 2).")
        return
    if not p.all_pages():
        st.info("Chưa có dữ liệu giao diện thực tế – điểm chỉ dựa trên mô tả UC (số bước, loại thao tác CRUD/xử lý dữ liệu).")

    weights = p.weights or cx.default_weights()
    h1, h2 = st.columns([3, 1], vertical_alignment="bottom")
    h1.subheader("4a. Cấu hình chấm điểm")
    with h2.popover("❔ Giải thích", width="stretch", help="Giải thích trọng số, mức trần và cách tính điểm"):
        score_guide(weights, p.score_caps)
    with st.expander("⚖️ Trọng số tiêu chí & mức trần", expanded=not p.scores):
        st.caption("Trọng số là giá trị **do bạn đặt** – ứng dụng không tự thay đổi. Kéo thanh trượt hoặc đổi mức trần "
                   "**chưa có tác dụng** cho tới khi bấm **🧮 Tính điểm & đề xuất**.")
        cols = st.columns(len(cx.CRITERIA))
        new_w = {k: cols[i].slider(label, 0.0, 1.0, float(weights.get(k, 0.1)), 0.05, key=f"w_{p.id}_{k}",
                                   help=HOW[k]) for i, (k, label) in enumerate(cx.CRITERIA.items())}
        c1, c2, c3 = st.columns([1, 1, 2])
        by_mod = c2.checkbox("Đề xuất theo từng phân hệ", False, key=f"by_mod_{p.id}",
                             help="Mỗi phân hệ đề xuất tối đa N UC điểm cao nhất (thay cho 'Số UC đề xuất' toàn dự án).")
        per_mod_n = c2.number_input("Số UC mỗi phân hệ", 1, 5, 2, key=f"per_mod_n_{p.id}", disabled=not by_mod)
        top_n = c1.number_input("Số UC đề xuất", 1, 30, int(config.get("scoring.top_n", 3)), disabled=by_mod,
                                key=f"top_n_{p.id}",
                                help="Tổng số UC đề xuất trên toàn dự án (khi không đề xuất theo phân hệ).")
        per_module = int(per_mod_n) if by_mod else 0
        caps_mode = c3.radio("Mức trần để quy về thang 0–1", list(CAPS_MODES),
                             index=list(CAPS_MODES).index(p.caps_mode if p.caps_mode in CAPS_MODES else "auto"),
                             format_func=CAPS_MODES.get, key=f"caps_mode_{p.id}",
                             help="Chỉ thay đổi mức trần (dòng bên dưới) và điểm trong bảng kết quả – không thay đổi trọng số.")
        if p.score_caps:
            st.caption("Mức trần **đang dùng** (lần tính gần nhất, chế độ "
                       f"{'tự động' if p.caps_mode == 'auto' else 'cố định'}): " + _caps_text(p.score_caps))
        if p.scores and p.score_caps and caps_mode != (p.caps_mode or "auto"):
            preview = cx.compute_caps([s.raw for s in p.scores], caps_mode)
            st.caption(f"Mức trần **sẽ dùng** nếu áp dụng chế độ {'tự động' if caps_mode == 'auto' else 'cố định'}: "
                       + _caps_text(preview))

    changed = []
    if p.scores:
        if any(abs(new_w[k] - float(weights.get(k, 0))) > 1e-9 for k in cx.CRITERIA):
            changed.append("trọng số")
        if caps_mode != (p.caps_mode or "auto"):
            changed.append("mức trần")
    b1, b2 = st.columns([1, 2], vertical_alignment="center")
    clicked = b1.button("🧮 Tính điểm & đề xuất", type="primary", width="stretch")
    if changed:
        b2.warning(f"Bạn đã đổi **{' và '.join(changed)}** nhưng chưa áp dụng – bấm *Tính điểm & đề xuất* để chấm lại.")
    if clicked:
        old_top = [s.uc_code for s in sorted(p.scores, key=lambda x: x.total, reverse=True)[:3]]
        p.weights, p.caps_mode = new_w, caps_mode
        p.scores = cx.recommend(cx.score_project(p, new_w, caps_mode), int(top_n), per_module)
        p.confirmed = False
        state.save(p)
        new_top = [s.uc_code for s in sorted(p.scores, key=lambda x: x.total, reverse=True)[:3]]
        st.session_state[f"score_msg_{p.id}"] = (
            f"Đã chấm lại **{len(p.scores)} UC** – mức trần {'tự động' if caps_mode == 'auto' else 'cố định'}, "
            f"trọng số {' · '.join(f'{cx.CRITERIA[k]} {v:.2f}' for k, v in new_w.items())}. "
            + (f"Top 3: {', '.join(new_top)}" + (f" (trước đó: {', '.join(old_top)})." if old_top and old_top != new_top
                                                  else " (không đổi)." if old_top else ".")))
        st.rerun()
    msg = st.session_state.pop(f"score_msg_{p.id}", None)
    if msg:
        st.success(msg, icon="✅")

    if not p.scores:
        return
    if not p.score_caps:
        st.info("Kết quả dưới đây được chấm bằng phiên bản cũ. Bấm **Tính điểm & đề xuất** để chấm lại theo cách tính mới "
                "(đếm loại thao tác, mức trần tự động).")
    st.subheader("4b. Kết quả chấm điểm")
    st.caption("Điểm 0–100 = tổng có trọng số của các tiêu chí đã quy về thang 0–1. Tick cột **Chọn** để điều chỉnh UC "
               "sẽ kiểm thử.")
    used_w = p.weights or weights
    view = st.radio("Cột tiêu chí hiển thị", ["raw", "score", "contrib"], horizontal=True, key=f"view_{p.id}",
                    format_func={"raw": "Số liệu thô", "score": "Điểm 0–1", "contrib": "Điểm đóng góp (/100)"}.get)
    df_all = scores_df(p, view, used_w)

    # ------------------------------------------------ dòng tóm tắt
    n_mod = len({s.module or "—" for s in p.scores})
    n_match = sum(1 for s in p.scores if s.matched_pages)
    n_rec = sum(1 for s in p.scores if s.recommended)
    n_sel_all = sum(1 for s in p.scores if s.selected)

    # ------------------------------------------------ bộ lọc theo cột
    with st.container(border=True):
        st.markdown("**🔎 Lọc danh sách UC**")
        f1, f2, f3 = st.columns([2, 2, 1])
        mod_opts = sorted(df_all["Tên phân hệ"].unique())
        f_mod = f1.multiselect("Tên phân hệ", mod_opts, key=f"f_mod_{p.id}", placeholder="Tất cả phân hệ")
        web_opts = sorted({w.strip() for v in df_all["Phân hệ trên web"] for w in str(v).split(",") if w.strip()})
        f_web = f2.multiselect("Phân hệ trên web", web_opts, key=f"f_web_{p.id}", placeholder="Tất cả")
        f_text = f3.text_input("Mã / tên UC chứa", key=f"f_text_{p.id}", placeholder="vd: thống kê")
        g1, g2, g3 = st.columns([2, 2, 1])
        f_state = g1.radio("Trạng thái", ["all", "rec", "sel", "unsel"], horizontal=True, key=f"f_state_{p.id}",
                           format_func={"all": "Tất cả", "rec": "Đề xuất ⭐", "sel": "Đang chọn",
                                        "unsel": "Chưa chọn"}.get)
        f_match = g2.radio("Ghép màn hình (bước 3)", ["all", "yes", "no"], horizontal=True, key=f"f_match_{p.id}",
                           format_func={"all": "Tất cả", "yes": "Đã ghép", "no": "Chưa ghép"}.get)
        f_score = g3.slider("Điểm từ", 0, 100, 0, 5, key=f"f_score_{p.id}")
    df = df_all
    if f_mod:
        df = df[df["Tên phân hệ"].isin(f_mod)]
    if f_web:
        df = df[df["Phân hệ trên web"].apply(lambda v: any(w in str(v).split(", ") for w in f_web))]
    if f_text.strip():
        t = f_text.strip().lower()
        df = df[df["Mã UC"].str.lower().str.contains(t, regex=False) | df["Tên UC"].str.lower().str.contains(t, regex=False)]
    if f_state == "rec":
        df = df[df["Đề xuất"] == "⭐"]
    elif f_state == "sel":
        df = df[df["Chọn"]]
    elif f_state == "unsel":
        df = df[~df["Chọn"]]
    if f_match == "yes":
        df = df[df["Số màn hình ghép"] > 0]
    elif f_match == "no":
        df = df[df["Số màn hình ghép"] == 0]
    if f_score:
        df = df[df["Điểm"] >= f_score]
    df = df.reset_index(drop=True)
    filtered = len(df) < len(df_all)

    st.markdown(f"📋 **{len(p.scores)}** UC · **{n_mod}** phân hệ · **{n_match}** UC ghép được màn hình · "
                f"**{n_rec}** UC đề xuất ⭐ · **{n_sel_all}** UC đang chọn"
                + (f" · đang hiển thị **{len(df)}/{len(df_all)}** UC theo bộ lọc" if filtered else ""))

    # ------------------------------------------------ biểu đồ điểm (sắp theo điểm, tô màu UC đề xuất)
    with st.expander("📈 Biểu đồ điểm UC", expanded=True):
        n_opts = {10: "Top 10", 20: "Top 20", 30: "Top 30", 0: "Tất cả"}
        n_show = st.radio("Hiển thị", list(n_opts), horizontal=True, key=f"chart_n_{p.id}", format_func=n_opts.get,
                          label_visibility="collapsed")
        cdf = df.sort_values("Điểm", ascending=False)
        cdf = cdf if not n_show else cdf.head(n_show)
        if cdf.empty:
            st.caption("Không có UC nào khớp bộ lọc.")
        else:
            import altair as alt
            cdf = cdf.assign(**{"Loại": cdf["Đề xuất"].map(lambda v: "Đề xuất ⭐" if v else "Khác")})
            # chiều cao theo số UC (mỗi UC 1 hàng 24px) – thanh tự co theo hàng, không chồng lên nhau khi ít UC
            chart = alt.Chart(cdf).mark_bar(cornerRadiusEnd=3).encode(
                x=alt.X("Điểm:Q", scale=alt.Scale(domain=[0, 100]), title="Điểm độ phức tạp (0–100)",
                        axis=alt.Axis(tickCount=10)),
                y=alt.Y("Mã UC:N", sort="-x", title=None),
                color=alt.Color("Loại:N", scale=alt.Scale(domain=["Đề xuất ⭐", "Khác"], range=["#E08A1E", "#1F4E9C"]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=["Mã UC", "Tên UC", "Tên phân hệ", alt.Tooltip("Điểm:Q", format=".1f"), "Loại"],
            ).properties(height=alt.Step(24))
            st.altair_chart(chart, width="stretch")
            st.caption(f"{n_opts[n_show] if n_show else 'Tất cả'} UC điểm cao nhất"
                       + (" trong bộ lọc hiện tại" if filtered else "") + f" ({len(cdf)} UC), sắp xếp theo điểm giảm dần.")

    crit_cfg = {}
    for label in cx.CRITERIA.values():
        if view == "score":
            crit_cfg[label] = st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")
        elif view == "contrib":
            crit_cfg[label] = st.column_config.NumberColumn(format="%.1f")
    disabled = [c for c in df.columns if c != "Chọn"]
    fsig = abs(hash((tuple(f_mod), tuple(f_web), f_text, f_state, f_match, f_score))) % 10**8
    val = check_all_buttons(f"uc_chk_{p.id}", len(df), help="Áp dụng cho các UC đang hiển thị theo bộ lọc – "
                            "UC bị lọc ẩn giữ nguyên lựa chọn. Lưu ngay, không cần bấm Lưu lựa chọn.")
    if val is not None:
        shown = set(df["Mã UC"])
        for s in p.scores:
            if s.uc_code in shown:
                s.selected = val
        p.confirmed = False
        state.save(p)
        st.session_state.pop(f"score_editor_{p.id}_{view}_{fsig}", None)
        st.rerun()
    ed = st.data_editor(df, width="stretch", hide_index=True, disabled=disabled, height=420,
                        key=f"score_editor_{p.id}_{view}_{fsig}",
                        column_config={
                            "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Độ tin cậy ghép": st.column_config.NumberColumn(format="%.2f"),
                            "Mã UC": st.column_config.TextColumn(pinned=True),
                            "Tên UC": st.column_config.TextColumn(width="medium"),
                            "Tên phân hệ": st.column_config.TextColumn(
                                width="medium", help="Phân hệ của UC theo file danh sách UC (bước 2)"),
                            "Phân hệ trên web": st.column_config.TextColumn(
                                width="small", help="Phân hệ (menu) trên hệ thống web chứa màn hình đã ghép với UC (bước 3)"),
                            "Lý do": st.column_config.TextColumn(width="large"), **crit_cfg})
    st.caption("Bấm tiêu đề cột để **sắp xếp**; biểu tượng 🔍 trên thanh công cụ của bảng để tìm nhanh. "
               "Tick cột **Chọn** rồi bấm **Lưu lựa chọn** *trước khi đổi bộ lọc*. Lọc chỉ ẩn bớt dòng – lựa chọn của "
               "các UC đang bị ẩn được giữ nguyên.")
    if p.scores:
        mods = pd.DataFrame([{"Tên phân hệ": s.module or "—", "UC": 1, "Đề xuất": int(s.recommended),
                              "Có API": int(bool(s.api_paths))} for s in p.scores])
        summ = mods.groupby("Tên phân hệ", sort=False).sum().reset_index()
        with st.expander(f"📊 Đề xuất theo phân hệ – {int(summ['Đề xuất'].gt(0).sum())}/{len(summ)} phân hệ có UC được đề xuất"):
            summ["Ghi chú"] = [
                "" if r["Đề xuất"] else ("Không có UC ghép được màn hình/API ở bước 3 (hệ thống ngoài, thiếu quyền, "
                                         "tên menu khác…) – ghép tay hoặc thêm phân hệ ở bước 3" if not r["Có API"]
                                         else "Các UC trùng màn hình/bộ API với UC đã chọn")
                for _, r in summ.iterrows()]
            st.dataframe(summ.rename(columns={"UC": "Số UC", "Đề xuất": "Số UC đề xuất", "Có API": "UC có API"}),
                         hide_index=True, width="stretch")
            st.caption("Không đề xuất 2 UC dùng cùng bộ màn hình hoặc bộ API trùng > 60% trong cùng phân hệ (sẽ sinh script trùng nhau), nên có "
                       "phân hệ nhận ít UC hơn số yêu cầu. Có thể tick thêm UC thủ công ở cột **Chọn**.")
    visible = set(ed["Mã UC"])
    sel_visible = set(ed.loc[ed["Chọn"], "Mã UC"])
    n_sel = len(sel_visible) + sum(1 for s in p.scores if s.selected and s.uc_code not in visible)
    c1, c2 = st.columns([1, 3])
    if c1.button(f"💾 Lưu lựa chọn ({n_sel} UC)", type="primary"):
        for s in p.scores:
            if s.uc_code in visible:             # chỉ cập nhật các dòng đang hiển thị; dòng bị lọc ẩn giữ nguyên
                s.selected = s.uc_code in sel_visible
        p.confirmed = False
        state.save(p)
        st.session_state[f"sel_saved_{p.id}"] = True
        st.rerun()                                # cập nhật dòng tóm tắt + dấu ✅ bước 5 trên thanh bên ngay
    if st.session_state.pop(f"sel_saved_{p.id}", False):
        c2.success("Đã lưu lựa chọn.")
    if n_sel > 3 and not by_mod:
        c2.warning("Khuyến nghị chọn 2–3 UC để tập trung phân tích.")
    elif n_sel > 3:
        c2.info(f"Đang chọn {n_sel} UC – mỗi UC sẽ chạy 1 lượt/công cụ ở bước 7 (tuần tự), hãy tính thời gian chạy.")

    with st.expander("🔍 Vì sao UC này được điểm như vậy?", expanded=False):
        ranked = sorted(p.scores, key=lambda x: x.total, reverse=True)
        default = next((i for i, s in enumerate(ranked) if s.selected), 0)
        code = st.selectbox("Chọn UC", [s.uc_code for s in ranked], index=default, key=f"why_uc_{p.id}",
                            format_func=lambda c: f"{c} – {next(s for s in ranked if s.uc_code == c).uc_name} "
                                                  f"({next(s for s in ranked if s.uc_code == c).total:.1f} điểm)")
        breakdown(p, next(s for s in ranked if s.uc_code == code), used_w)

    # ghép màn hình thủ công
    pages = p.all_pages()
    if pages:
        with st.expander("🔗 Điều chỉnh màn hình ghép cho UC (khi ghép tự động chưa đúng)"):
            codes = [s.uc_code for s in p.scores if s.selected] or [s.uc_code for s in p.scores[:5]]
            code = st.selectbox("UC", codes, format_func=lambda c: f"{c} – {p.uc(c).name if p.uc(c) else ''}",
                                key=f"mm_uc_{p.id}")
            sc = next(s for s in p.scores if s.uc_code == code)
            opts = {pg.url: f"[{pg.module}] {pg.menu_text or pg.title} – {pg.url}" for pg in pages}
            chosen = st.multiselect("Màn hình liên quan", list(opts), default=[u for u in sc.matched_pages if u in opts],
                                    format_func=lambda u: opts[u], key=f"mm_pages_{p.id}_{code}")
            if st.button("Áp dụng & tính lại điểm"):
                sc.matched_pages, sc.note = chosen, "manual-match"
                sel = {s.uc_code for s in p.scores if s.selected}
                rec = {s.uc_code for s in p.scores if s.recommended}
                p.scores = cx.score_project(p, p.weights or new_w)
                for s in p.scores:
                    s.selected, s.recommended = s.uc_code in sel, s.uc_code in rec
                state.save(p)
                st.rerun()

    state.nav_buttons("crawl", "confirm", next_disabled=not any(s.selected for s in p.scores))
