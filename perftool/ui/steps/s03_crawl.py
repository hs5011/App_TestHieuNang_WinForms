"""Bước 3 - Đăng nhập hệ thống, phát hiện phân hệ và phân tích giao diện thực tế."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from ... import jobs
from ...crawler.guard import LEVELS as GUARD_LEVELS
from ...crawler.interactions import join_url
from ...crawler.merge import (apply_record, clear_record, delete_session, load_segments, load_sessions, merge_analysis,
                              merge_discover, segment_status)
from ...models import INTERACTION_KINDS, ModuleInfo, Project
from ...storage import sub_dir
from .. import state
from ..components import check_all_buttons, job_panel, section_titles


def _merge_discover(p: Project) -> None:
    state.save(merge_discover(p))


def _merge_analysis(p: Project) -> None:
    s = jobs.read_status(p.id, "crawl_analyze") or {}
    if s.get("status") == "stopped":
        import json
        f = sub_dir(p.id, "crawl") / "analysis.json"
        # chỉ gộp kết quả dở của CHÍNH lần quét bị dừng (file định dạng mới có "partial"); không gộp lại kết quả cũ
        if not f.exists() or "partial" not in json.loads(f.read_text(encoding="utf-8"))                 or f.stat().st_mtime < _ts(s.get("started", "")):
            return
    state.save(merge_analysis(p))


def _ts(text: str) -> float:
    from datetime import datetime
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def _merge_record(p: Project) -> None:
    apply_record(p)
    state.save(p)


def _auto_merge(p: Project, job: str, merge_fn, statuses: tuple = ("done",)) -> None:
    s = jobs.read_status(p.id, job)
    if s and s.get("status") in statuses and not s.get("merged"):
        try:
            merge_fn(p)
        except FileNotFoundError:        # bị dừng trước khi có kết quả nào
            pass
        jobs.write_status(p.id, job, merged=True)
        st.rerun()                       # vẽ lại cả thanh bên (dấu ✅ bước 3) theo kết quả vừa gộp


def render(p: Project) -> None:
    if p.kind == "winforms":
        from . import s03_winforms
        return s03_winforms.render(p)
    st.header("3. Đăng nhập & phân tích giao diện Web")
    st.caption("Crawler điều hướng qua menu/link và đọc giao diện – không bấm Lưu/Xoá, không gửi form nghiệp vụ. "
               "Có thể bật tự quét tương tác (3b) và ghi thao tác thủ công (3c) để bổ sung popup, menu ⋮, trang con.")
    if not p.login.base_url:
        st.warning("Chưa khai báo URL hệ thống ở bước 1.")
        return
    pwd = state.password(p)
    _auto_merge(p, "crawl_discover", _merge_discover)
    # quét phân hệ lưu dần analysis.json sau mỗi phân hệ -> bấm ⏹ Dừng vẫn giữ các phân hệ đã quét xong
    _auto_merge(p, "crawl_analyze", _merge_analysis, ("done", "stopped"))
    # ghi thủ công lưu dần vào record.json -> bấm ⏹ Dừng (tiến trình bị dừng) vẫn gộp được phần đã ghi
    _auto_merge(p, "crawl_record", _merge_record, ("done", "stopped", "failed"))
    recording = jobs.is_running(p.id, "crawl_record")
    busy = jobs.is_running(p.id, "crawl_discover") or jobs.is_running(p.id, "crawl_analyze") or recording

    sec = section_titles(3)
    # ---------------------------------------------------------------- 3a. phát hiện phân hệ
    sec("Đăng nhập & phát hiện phân hệ")
    c1, c2, c3 = st.columns([2, 2, 3])
    fresh = c2.checkbox("Đăng nhập lại (bỏ phiên đã lưu)", False)
    c3.caption(f"URL: {p.login.base_url} · Tài khoản: {p.login.username}"
               + (" · Đăng nhập thủ công" if p.login.manual_login else ""))
    if c1.button("🔐 Đăng nhập & phát hiện phân hệ", type="primary", disabled=busy, width="stretch"):
        if not pwd and not p.login.manual_login:
            st.error("Chưa có mật khẩu – quay lại bước 1.")
        else:
            jobs.start_job(p.id, "crawl_discover", "perftool.crawler.cli",
                           ["--project", p.id, "--mode", "discover"] + (["--fresh-login"] if fresh else []),
                           env={"PERFTOOL_PASSWORD": pwd})
            st.rerun()
    job_panel(p.id, "crawl_discover", "Phát hiện phân hệ", height=180)

    if p.auth.login_url or p.auth.static_headers:
        with st.expander("🔑 Thông tin xác thực đã nhận diện (dùng để sinh script)"):
            st.write(f"**Request đăng nhập:** `{p.auth.login_method} {p.auth.login_url or '—'}`")
            st.write(f"**Đường dẫn token trong JSON:** `{p.auth.token_json_path or '—'}`")
            st.write(f"**Header tĩnh:** {', '.join(p.auth.static_headers) or '—'} · **Cookie:** {len(p.auth.cookies)}")

    # ---------------------------------------------------------------- 3b. danh sách phân hệ
    sec("Danh sách phân hệ cần phân tích")
    st.caption("Tick chọn phân hệ cần quét. Có thể thêm dòng để khai báo phân hệ/URL thủ công.")
    df = pd.DataFrame([{"Chọn": m.enabled, "Phân hệ": m.name, "URL": m.url, "Số màn hình đã quét": len(m.pages)}
                       for m in p.modules] or [{"Chọn": True, "Phân hệ": "", "URL": "", "Số màn hình đã quét": 0}])
    val = check_all_buttons(f"mod_chk_{p.id}", len(p.modules))
    if val is not None:
        for m in p.modules:
            m.enabled = val
        state.save(p)
        st.session_state.pop(f"mod_editor_{p.id}", None)
        st.rerun()
    ed = st.data_editor(df, num_rows="dynamic", width="stretch", key=f"mod_editor_{p.id}",
                        column_config={"Số màn hình đã quét": st.column_config.NumberColumn(disabled=True),
                                       "URL": st.column_config.LinkColumn()})
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("💾 Lưu danh sách", width="stretch"):
        old = {m.name: m for m in p.modules}
        new = []
        for _, r in ed.iterrows():
            name, url = str(r["Phân hệ"] or "").strip(), str(r["URL"] or "").strip()
            if not name or not url or name == "nan":
                continue
            if not url.startswith("http"):
                url = join_url(p.login.base_url, url)
            m = old.get(name, ModuleInfo(name=name, url=url))
            m.url, m.enabled = url, bool(r["Chọn"])
            new.append(m)
        p.modules = new
        state.save(p)
        st.success("Đã lưu.")
        st.rerun()
    pop = st.checkbox("Tự quét tương tác trên màn hình: nút Xem / Thêm mới / Sửa, menu ⋮ trên dòng đầu bảng, tab, "
                      "Tìm kiếm, chuyển trang", p.crawl_popups, key=f"crawl_popups_{p.id}",
                      help="Nhiều màn hình mở dạng popup hoặc từ menu ⋮ của từng dòng (Xử lý, Xin gia hạn, Phân công…) nên "
                           "không có link trên menu. Khi bật, crawler bấm thử: tab, nút Tìm kiếm, sang trang 2, nút mở "
                           "popup Xem/Thêm mới/Sửa/Chi tiết, và mở menu ⋮ ở DÒNG ĐẦU TIÊN của bảng rồi bấm lần lượt từng "
                           "mục an toàn. Popup có biểu mẫu/bảng/nội dung chi tiết và trang con mở từ menu được tính là màn "
                           "hình; API gọi khi mở được đưa vào kịch bản test.")
    if pop != p.crawl_popups:
        p.crawl_popups = pop
        state.save(p)
    if pop:
        st.caption("⚠️ Chế độ này **có bấm nút** trên hệ thống thật – chỉ bật khi được phép. Biện pháp an toàn: không bấm nút "
                   "bên trong popup (Lưu/Gửi…), không bấm mục nguy hiểm (Xoá, Duyệt, Hoàn thành, Tiếp nhận… – "
                   "`crawler.popup_blacklist`), và **bộ chặn ghi** huỷ mọi request ghi dữ liệu phát sinh ngoài ý muốn "
                   "trước khi tới máy chủ (`crawler.write_guard`).")
    n_sel = sum(1 for m in p.modules if m.enabled)
    if c2.button(f"🔍 Phân tích {n_sel} phân hệ", type="primary", disabled=busy or n_sel == 0, width="stretch"):
        jobs.start_job(p.id, "crawl_analyze", "perftool.crawler.cli", ["--project", p.id, "--mode", "analyze"],
                       env={"PERFTOOL_PASSWORD": pwd})
        st.rerun()
    c3.caption("Mỗi phân hệ quét tối đa `crawler.max_pages_per_module` màn hình (config/settings.yaml).")
    job_panel(p.id, "crawl_analyze", "Phân tích phân hệ", height=260)

    # ---------------------------------------------------------------- 3c. ghi thao tác thủ công
    sec("Ghi thao tác thủ công (tuỳ chọn)")
    _record_section(p, pwd, busy, recording)

    # ---------------------------------------------------------------- 3d. kết quả
    pages = p.all_pages()
    if pages:
        sec("Kết quả phân tích giao diện")
        apis = [r for pg in pages for r in pg.api_requests]
        durs = [r.duration_ms for r in apis if r.duration_ms]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Phân hệ đã quét", sum(1 for m in p.modules if m.pages))
        n_pop = sum(1 for pg in pages for pp in pg.popups if pp.counted)
        m2.metric("Màn hình", len(pages) + n_pop,
                  help=f"{len(pages)} trang + {n_pop} popup / trang con mở từ nút, menu ⋮" if n_pop else None)
        m3.metric("API/XHR ghi nhận", len(apis))
        m4.metric("Thời gian phản hồi API TB", f"{sum(durs) / len(durs):.0f} ms" if durs else "—")
        for m in p.modules:
            if not m.pages:
                continue
            n_mpop = sum(1 for pg in m.pages for pp in pg.popups if pp.counted)
            with st.expander(f"📁 {m.name} – {len(m.pages)} trang"
                             + (f" + {n_mpop} popup / trang con" if n_mpop else "")):
                rows = [{"Màn hình": pg.menu_text or pg.title, "Tiêu đề": pg.title, "URL": pg.url,
                         "Nguồn": _source(pg), "API": len(pg.api_requests),
                         "Popup / trang con": sum(1 for pp in pg.popups if pp.counted),
                         "Input": pg.inputs, "Bảng": pg.tables, "Dòng": pg.table_rows,
                         "Nút CRUD": ", ".join(pg.crud_buttons[:6]),
                         "Tải trang (ms)": pg.load_time_ms,
                         "API chậm nhất (ms)": max([r.duration_ms or 0 for r in pg.api_requests], default=0),
                         "Lỗi": pg.error} for pg in m.pages]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                             column_config={"URL": st.column_config.LinkColumn()})
                inter = [_interaction_row(pg, pp) for pg in m.pages for pp in pg.popups]
                if inter:
                    st.caption(f"**Popup / trang con / tương tác đã quét ({len(inter)})** – mỗi dòng là 1 lần bấm "
                               "(nút, mục menu ⋮, tab, Tìm kiếm, chuyển trang) hoặc 1 popup ghi thủ công. "
                               "Cột *Tính màn hình* = được cộng vào tiêu chí Số màn hình ở bước 4.")
                    st.dataframe(pd.DataFrame(inter), width="stretch", hide_index=True,
                                 column_config={"URL trang con": st.column_config.LinkColumn()})
                shots = [pg for pg in m.pages if pg.screenshot and Path(pg.screenshot).exists()]
                if shots:
                    cols = st.columns(4)
                    for i, pg in enumerate(shots[:8]):
                        cols[i % 4].image(pg.screenshot, caption=(pg.menu_text or pg.title)[:40], width="stretch")

    state.nav_buttons("import", "score", next_disabled=False,
                      next_label="Tiếp tục ➜" if pages else "Bỏ qua (chỉ dùng mô tả UC) ➜")


def _source(pg) -> str:
    if pg.origin == "manual":
        return "Ghi thủ công"
    return "Tự quét + ghi thủ công" if any(r.origin == "manual" for r in pg.requests) else "Tự quét"


def _interaction_row(pg, pp) -> dict:
    return {"Trên màn hình": (pg.menu_text or pg.title)[:60], "Loại": INTERACTION_KINDS.get(pp.kind, pp.kind),
            "Nút / mục đã bấm": pp.trigger, "Mở ra": pp.title if pp.kind in ("popup", "page") else "",
            "Trường nhập": pp.inputs + pp.selects, "Bảng": pp.tables, "API": pp.api_count,
            "Tính màn hình": "✔" if pp.counted else "", "Nguồn": "Ghi thủ công" if pp.origin == "manual" else "Tự quét",
            "URL trang con": pp.url}


def _record_section(p: Project, pwd: str, busy: bool, recording: bool) -> None:
    st.caption("**Không bắt buộc** – nếu kết quả 3b/3d đã đủ các màn hình cần test thì bỏ qua mục này. "
               "Dùng để bổ sung những màn hình crawler tự quét chưa tới được (popup nhiều bước, menu ⋮, màn hình cần chọn "
               "dữ liệu trước…). PerfTool mở trình duyệt đã đăng nhập, **bạn tự bấm** qua các màn hình; công cụ ghi lại "
               "từng màn hình/popup cùng các API được gọi. Khi xong, bấm **Kết thúc ghi** ở góc dưới phải trình duyệt "
               "(hoặc nút bên dưới). Có thể ghi nhiều lần; kết quả được gộp vào bảng 3d và giữ lại khi phân tích lại 3b.")
    mods = ["(Tự nhận diện theo URL)"] + [m.name for m in p.modules]
    c1, c2 = st.columns(2)
    mod = c1.selectbox("Gán màn hình ghi được vào phân hệ", mods, key=f"rec_mod_{p.id}", disabled=busy,
                       help="Tự nhận diện: màn hình trùng URL đã quét -> phân hệ đó; URL mới -> phân hệ có đường dẫn gần "
                            "nhất, nếu không có thì vào phân hệ 'Ghi thao tác thủ công'.")
    chosen = next((m for m in p.modules if m.name == mod), None)
    start = c2.text_input("Trang mở đầu tiên", chosen.url if chosen else p.login.base_url,
                          key=f"rec_start_{p.id}_{mod}", disabled=busy)
    level = st.radio("Bảo vệ dữ liệu khi ghi", list(GUARD_LEVELS), format_func=GUARD_LEVELS.get, key=f"rec_guard_{p.id}",
                     horizontal=True, disabled=busy,
                     help="Request ghi dữ liệu bị huỷ trước khi tới máy chủ nhưng vẫn được ghi nhận để sinh kịch bản "
                          "(mặc định tắt trong kịch bản). Mức 'mọi request không phải GET' an toàn nhất nhưng có thể làm "
                          "một số màn hình tra cứu dùng POST không hiện dữ liệu.")
    if level == "off":
        st.warning("Bộ chặn ghi đang TẮT: mọi thao tác Lưu/Gửi/Duyệt/Xoá bạn bấm sẽ **ghi dữ liệu thật** vào hệ thống.")
    b1, b2, _ = st.columns([1, 1, 1])
    if b1.button("🎥 Bắt đầu ghi", type="primary", disabled=busy, width="stretch", key=f"rec_start_btn_{p.id}"):
        if not pwd and not p.login.manual_login:
            st.error("Chưa có mật khẩu – quay lại bước 1.")
        else:
            args = ["--project", p.id, "--mode", "record", "--guard", level, "--start-url", start.strip()]
            if chosen:
                args += ["--module", chosen.name]
            jobs.start_job(p.id, "crawl_record", "perftool.crawler.cli", args, env={"PERFTOOL_PASSWORD": pwd})
            st.rerun()
    if b2.button("⏹ Kết thúc ghi & lưu", disabled=not recording, width="stretch", key=f"rec_stop_btn_{p.id}"):
        (sub_dir(p.id, "crawl") / "record.stop").write_text("stop", encoding="utf-8")
        st.toast("Đã gửi yêu cầu kết thúc ghi – chờ vài giây để lưu.")
    job_panel(p.id, "crawl_record", "Ghi thao tác thủ công", height=220)

    segs = load_segments(p)
    if not segs:
        return
    sessions = len({s["session"] for s in segs})
    n_req = sum(len(s.get("requests", [])) for s in segs)
    n_blk = sum(1 for s in segs for r in s.get("requests", []) if r.get("blocked"))
    deleted = st.session_state.pop(f"_rec_deleted_{p.id}", None)
    if deleted:
        st.success(f"Đã xoá {deleted} và gộp lại kết quả 3d từ các lần ghi còn lại.")
    with st.expander(f"🎥 Dữ liệu đã ghi: {sessions} lần ghi · {len(segs)} màn hình/popup · {n_req} request"
                     + (f" · 🛡 {n_blk} request ghi bị chặn" if n_blk else ""), expanded=bool(deleted)):
        _record_sessions(p, [s for s in load_sessions(p) if s.get("segments")], busy)
        st.divider()
        c1, c2, _ = st.columns([1, 1, 3])
        ok = c2.checkbox("Xác nhận xoá tất cả", key=f"rec_clear_ok_{p.id}")
        if c1.button("🗑 Xoá tất cả lần ghi", disabled=busy or not ok, width="stretch", key=f"rec_clear_{p.id}"):
            clear_record(p)
            state.save(p)
            st.success("Đã xoá dữ liệu ghi thủ công.")
            st.rerun()


def _session_label(i: int, s: dict) -> str:
    t0, t1 = str(s.get("started", "")), str(s.get("finished", ""))
    span = t0[11:19] + (f"–{t1[11:19]}" if t1[11:19] else "")
    return f"Lần {i} · {t0[:10]} {span}" + (f" · {s['module']}" if s.get("module") else "")


def _record_sessions(p: Project, sessions: list[dict], busy: bool) -> None:
    """Bảng tóm tắt từng lần ghi + chi tiết 1 lần được chọn (màn hình/popup, trạng thái gộp, API) + nút xoá riêng lần đó.
    Đánh số theo thời gian: Lần 1 = lần ghi sớm nhất."""
    def apis(g: dict) -> int:
        return sum(1 for r in g.get("requests", []) if r.get("resource_type") in ("xhr", "fetch"))

    stats = []
    for i, s in enumerate(sessions, 1):
        segs, status = s.get("segments", []), segment_status(p, s)
        stats.append({"Lần ghi": f"Lần {i}", "Bắt đầu": s.get("started", ""), "Kết thúc": s.get("finished", ""),
                      "Phân hệ": s.get("module") or "(tự nhận diện)",
                      "Màn hình": sum(1 for g in segs if g.get("kind") != "popup"),
                      "Popup": sum(1 for g in segs if g.get("kind") == "popup"),
                      "Đưa vào 3d": sum(1 for x in status if x.startswith("✅")),
                      "Bỏ qua": sum(1 for x in status if x.startswith("⛔")),
                      "API": sum(apis(g) for g in segs),
                      "Request ghi bị chặn": sum(1 for g in segs for r in g.get("requests", []) if r.get("blocked"))})
    # bấm vào 1 dòng của bảng để xem chi tiết lần ghi đó (không còn nút chọn riêng)
    ev = st.dataframe(pd.DataFrame(stats), width="stretch", hide_index=True, on_select="rerun",
                      selection_mode="single-row", key=f"rec_pick_{p.id}_{len(sessions)}")
    rows = [r for r in (ev.selection.rows if ev and ev.selection else []) if r < len(sessions)]
    if not rows:
        st.caption("👆 Tick ô vuông ở đầu 1 dòng trong bảng để xem chi tiết và xoá lần ghi đó.")
        return
    pick = rows[0]
    s = sessions[pick]
    with st.container(border=True):
        st.markdown(f"**{_session_label(pick + 1, s)}**")
        st.dataframe(pd.DataFrame([{
            "STT": j, "Loại": "🪟 Popup" if g.get("kind") == "popup" else "📄 Màn hình", "Tiêu đề": g.get("title", ""),
            "Mở bằng": g.get("trigger", ""), "Kết quả gộp": stt, "API": apis(g),
            "Request ghi bị chặn": ", ".join(sorted({f"{r['method']} {r['url'].split('?')[0].rsplit('/', 1)[-1]}"
                                                     for r in g.get("requests", []) if r.get("blocked")})),
            "URL": g.get("url", "")} for j, (g, stt) in enumerate(zip(s.get("segments", []), segment_status(p, s)), 1)]),
            width="stretch", hide_index=True, column_config={"URL": st.column_config.LinkColumn()})
        c1, c2, _ = st.columns([1, 1, 3])
        ok = c2.checkbox(f"Xác nhận xoá lần {pick + 1}", key=f"rec_del_ok_{p.id}_{s.get('started', '')}")
        if c1.button(f"🗑 Xoá lần ghi {pick + 1}", disabled=busy or not ok, width="stretch",
                     key=f"rec_del_{p.id}_{s.get('started', '')}",
                     help="Xoá riêng lần ghi này (màn hình, popup, API đã ghi); các lần ghi khác giữ nguyên."):
            if delete_session(p, s.get("started", "")):
                state.save(p)
                st.session_state[f"_rec_deleted_{p.id}"] = _session_label(pick + 1, s)
            st.rerun()       # key bảng gắn số lần ghi -> sau khi xoá bảng được bỏ chọn
