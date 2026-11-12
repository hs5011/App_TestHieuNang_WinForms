"""Bước 3 (dự án WinForms) - Ghi nhận lưu lượng API của ứng dụng desktop và quy ra "màn hình".

Ứng dụng WinForms không có DOM để quét, nên PerfTool ghi lại các lời gọi API khi bạn thao tác (qua proxy ghi hoặc
file HAR) rồi chia thành màn hình. Kết quả dùng chung cho các bước 4–10 như dự án Web."""
from __future__ import annotations

from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from ... import jobs
from ...crawler import winforms as wf
from ...crawler.guard import LEVELS as GUARD_LEVELS
from ...crawler.merge import clear_record, delete_session, load_sessions
from ...crawler.winforms_merge import merge_winforms, rename_segments
from ...models import Project
from ...storage import sub_dir
from .. import state
from ..components import job_panel, section_titles

PROXY_HELP = (
    "1. **Bắt đầu ghi** bên dưới – PerfTool mở proxy tại `127.0.0.1:<cổng>`.\n"
    "2. Cho ứng dụng đi qua proxy, chọn **một** cách:\n"
    "   - Điền đường dẫn `.exe` vào ô *Mở ứng dụng kèm* (PerfTool đặt biến `HTTP_PROXY`/`HTTPS_PROXY`; "
    "ứng dụng .NET Core/.NET 5+ nhận biến này).\n"
    "   - Hoặc đặt **proxy hệ thống Windows** (Settings › Network › Proxy › Manual) = `127.0.0.1:<cổng>` – "
    "ứng dụng .NET Framework dùng proxy hệ thống. **Nhớ tắt lại sau khi ghi xong.**\n"
    "   - Hoặc thêm vào `App.config`: `<system.net><defaultProxy><proxy usesystemdefault=\"false\" "
    "proxyaddress=\"http://127.0.0.1:<cổng>\" bypassonlocal=\"false\"/></defaultProxy></system.net>`.\n"
    "3. Nếu ứng dụng gọi **HTTPS**: cài chứng chỉ gốc mitmproxy (`%USERPROFILE%\\.mitmproxy\\mitmproxy-ca-cert.cer`, "
    "có sau lần chạy proxy đầu tiên) vào *Trusted Root Certification Authorities* – **chỉ trên máy kiểm thử**. "
    "Ứng dụng có *certificate pinning* sẽ không ghi được → dùng HAR hoặc tắt pinning ở bản test.\n"
    "4. Thao tác ứng dụng: **mỗi khi mở một màn hình mới, nhập tên màn hình rồi bấm “📍 Bắt đầu”** "
    "(đặt tên gần với tên Use Case để bước 4 ghép đúng).\n"
    "5. Xong bấm **Kết thúc ghi & lưu**.\n\n"
    "Lời gọi không đi qua HTTP(S) (WCF net.tcp, .NET Remoting, kết nối thẳng SQL…) thì không ghi được bằng cách này.")


def _merge(p: Project) -> None:
    merge_winforms(p)
    state.save(p)


def _auto_merge(p: Project) -> None:
    s = jobs.read_status(p.id, "crawl_record")
    if s and s.get("status") in ("done", "stopped", "failed") and not s.get("merged"):
        try:
            _merge(p)
        except FileNotFoundError:
            pass
        jobs.write_status(p.id, "crawl_record", merged=True)
        st.rerun()


def render(p: Project) -> None:
    st.header("3. Ghi nhận lưu lượng API của ứng dụng WinForms")
    st.caption("WinForms không có giao diện web để quét. PerfTool ghi lại các lời gọi API khi bạn thao tác ứng dụng "
               "(proxy ghi hoặc file HAR), chia thành **màn hình**, rồi ước lượng độ phức tạp từ nội dung API. "
               "Chỉ ghi lưu lượng đi qua proxy – không điều khiển hay bấm giúp trên ứng dụng của bạn.")
    if not p.login.base_url:
        st.warning("Chưa khai báo URL máy chủ API ở bước 1.")
        return
    pwd = state.password(p)
    _auto_merge(p)
    recording = jobs.is_running(p.id, "crawl_record")
    work = sub_dir(p.id, "crawl")
    marker_file = work / "winforms_markers.jsonl"
    sec = section_titles(3)
    host = urlparse(p.login.base_url).hostname or ""

    # ---------------------------------------------------------------- 3a. proxy ghi
    sec("Ghi trực tiếp qua proxy PerfTool")
    with st.expander("📘 Cách trỏ ứng dụng WinForms qua proxy (đọc trước lần đầu)"):
        st.markdown(PROXY_HELP)
    c1, c2, c3 = st.columns([2, 1, 2])
    hosts = c1.text_input("Chỉ ghi máy chủ API (phân tách dấu phẩy; trống = tất cả)", host,
                          key=f"wf_hosts_{p.id}", disabled=recording)
    port = c2.number_input("Cổng proxy", 1024, 65535, 8899, key=f"wf_port_{p.id}", disabled=recording)
    exe = c3.text_input("Mở ứng dụng kèm (đường dẫn .exe, tuỳ chọn)", key=f"wf_exe_{p.id}", disabled=recording)
    level = st.radio("Bảo vệ dữ liệu khi ghi", list(GUARD_LEVELS), format_func=GUARD_LEVELS.get,
                     key=f"wf_guard_{p.id}", horizontal=True, disabled=recording,
                     help="Request ghi dữ liệu (Lưu/Xoá/Duyệt…) bị chặn (trả 403) ngay tại proxy, KHÔNG tới máy chủ, nhưng "
                          "vẫn được ghi nhận để sinh kịch bản (mặc định tắt trong kịch bản). Ứng dụng sẽ báo lỗi ở thao tác đó.")
    if level == "off":
        st.warning("Bộ chặn ghi đang TẮT: Lưu/Gửi/Duyệt/Xoá bạn bấm trong ứng dụng sẽ **ghi dữ liệu thật** vào máy chủ.")
    b1, b2, _ = st.columns([1, 1, 1])
    if b1.button("🎥 Bắt đầu ghi (mở proxy)", type="primary", disabled=recording, width="stretch",
                 key=f"wf_start_{p.id}"):
        args = ["--project", p.id, "--mode", "record", "--guard", level, "--port", str(int(port)),
                "--hosts", hosts.strip()]
        if exe.strip():
            args += ["--launch", exe.strip()]
        try:
            jobs.start_job(p.id, "crawl_record", "perftool.crawler.winforms_cli", args, env={"PERFTOOL_PASSWORD": pwd})
            st.rerun()
        except RuntimeError as e:
            st.error(str(e))
    if b2.button("⏹ Kết thúc ghi & lưu", disabled=not recording, width="stretch", key=f"wf_stop_{p.id}"):
        (work / "record.stop").write_text("stop", encoding="utf-8")
        st.toast("Đã gửi yêu cầu kết thúc ghi – chờ vài giây để lưu.")

    if recording:
        with st.container(border=True):
            st.markdown("**📍 Đánh dấu màn hình** – bấm khi bạn vừa chuyển sang một màn hình mới trong ứng dụng")
            m1, m2, m3 = st.columns([3, 2, 1])
            screen = m1.text_input("Tên màn hình", key=f"wf_screen_{p.id}", placeholder="VD: Danh sách hồ sơ")
            default_mod = st.session_state.get(f"wf_last_mod_{p.id}") or (p.modules[0].name if p.modules
                                                                           else wf.WINFORMS_MODULE)
            module = m2.text_input("Phân hệ", default_mod, key=f"wf_mod_{p.id}")
            m3.write("")
            if m3.button("📍 Bắt đầu", width="stretch", key=f"wf_mark_{p.id}", disabled=not screen.strip()):
                wf.add_marker(marker_file, screen.strip(), module.strip() or wf.WINFORMS_MODULE)
                st.session_state[f"wf_last_mod_{p.id}"] = module.strip()
                st.toast(f"Đã đánh dấu: {screen.strip()}")
            marks = wf.read_markers(marker_file)
            if marks:
                st.caption("Đã đánh dấu: " + " → ".join(m["screen"] for m in marks))
    job_panel(p.id, "crawl_record", "Ghi lưu lượng WinForms", height=220)

    # ---------------------------------------------------------------- 3b. nhập HAR
    sec("Hoặc nhập file HAR (Fiddler / Charles / mitmproxy / DevTools)")
    st.caption("Đã ghi bằng công cụ khác? Xuất HAR rồi nhập vào đây. HAR không có mốc đánh dấu nên PerfTool tách màn hình "
               "theo khoảng nghỉ giữa các thao tác – bạn đổi tên màn hình ở mục bên dưới.")
    up = st.file_uploader("File HAR", type=["har", "json"], key=f"wf_har_{p.id}", disabled=recording)
    h1, h2, h3 = st.columns([2, 2, 1])
    har_hosts = h1.text_input("Chỉ lấy máy chủ", host, key=f"wf_har_hosts_{p.id}", disabled=recording)
    gap = h2.slider("Khoảng nghỉ tách màn hình (giây)", 2, 60, int(wf.GAP_DEFAULT_S), key=f"wf_gap_{p.id}")
    h3.write("")
    if h3.button("📥 Nhập HAR", disabled=recording or up is None, width="stretch", key=f"wf_har_btn_{p.id}"):
        path = work / "import.har"
        path.write_bytes(up.getvalue())
        jobs.start_job(p.id, "crawl_record", "perftool.crawler.winforms_cli",
                       ["--project", p.id, "--mode", "har", "--har", str(path), "--hosts", har_hosts.strip(),
                        "--gap", str(gap)], env={"PERFTOOL_PASSWORD": pwd})
        st.rerun()

    # ---------------------------------------------------------------- 3c. đổi tên màn hình
    sessions = [s for s in load_sessions(p) if s.get("segments")]
    sec("Màn hình đã ghi – đổi tên / xoá")
    if not sessions:
        st.info("Chưa có dữ liệu. Ghi qua proxy hoặc nhập HAR ở trên.")
    else:
        rows = [{"Lần ghi": s.get("started", ""), "STT": i, "Phân hệ": s.get("module", ""),
                 "Tên màn hình": g.get("title", ""), "API": len(g.get("requests", [])),
                 "Ghi bị chặn": sum(1 for r in g.get("requests", []) if r.get("blocked"))}
                for s in sessions for i, g in enumerate(s["segments"])]
        ed = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", key=f"wf_names_{p.id}_{len(rows)}",
                            disabled=["Lần ghi", "STT", "Phân hệ", "API", "Ghi bị chặn"])
        if st.button("💾 Lưu tên màn hình", key=f"wf_rename_{p.id}", disabled=recording):
            edits = [{"session": r["Lần ghi"], "index": int(r["STT"]), "title": r["Tên màn hình"]}
                     for _, r in ed.iterrows()]
            if rename_segments(work / "record.json", edits):
                _merge(p)
                st.success("Đã đổi tên và gộp lại kết quả.")
                st.rerun()
            else:
                st.info("Không có thay đổi.")
        d1, d2, d3 = st.columns([2, 1, 1])
        which = d1.selectbox("Xoá cả một lần ghi", [s.get("started", "") for s in sessions], key=f"wf_delsel_{p.id}")
        ok = d2.checkbox("Xác nhận xoá", key=f"wf_delok_{p.id}")
        d3.write("")
        if d3.button("🗑 Xoá lần ghi", disabled=not ok or recording, key=f"wf_del_{p.id}"):
            if delete_session(p, which):
                state.save(p)
            st.rerun()
        if st.button("🗑 Xoá toàn bộ dữ liệu đã ghi", disabled=recording, key=f"wf_clear_{p.id}"):
            clear_record(p)
            state.save(p)
            st.rerun()

    # ---------------------------------------------------------------- 3d. kết quả
    pages = p.all_pages()
    if pages:
        sec("Kết quả – màn hình quy đổi từ API")
        apis = [r for pg in pages for r in pg.api_requests]
        durs = [r.duration_ms for r in apis if r.duration_ms]
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Phân hệ", sum(1 for m in p.modules if m.pages))
        a2.metric("Màn hình", len(pages))
        a3.metric("API ghi nhận", len(apis))
        a4.metric("Thời gian phản hồi TB", f"{sum(durs) / len(durs):.0f} ms" if durs else "—")
        st.caption("Số trường nhập / bảng / dòng là **ước lượng từ nội dung API** (trường trong body ghi, tham số lọc, "
                   "phản hồi dạng danh sách) – dùng để chấm điểm độ phức tạp ở bước 4, không phải số điều khiển thật trên form.")
        for m in p.modules:
            if not m.pages:
                continue
            with st.expander(f"📁 {m.name} – {len(m.pages)} màn hình", expanded=True):
                st.dataframe(pd.DataFrame([{
                    "Màn hình": pg.title, "API": len(pg.api_requests), "Trường nhập (ước lượng)": pg.inputs,
                    "Ô chọn": pg.selects, "Bảng": pg.tables, "Dòng lớn nhất": pg.table_rows,
                    "Thao tác ghi": ", ".join(pg.crud_buttons),
                    "API chậm nhất (ms)": max([r.duration_ms or 0 for r in pg.api_requests], default=0),
                    "Bị chặn": sum(1 for r in pg.requests if r.blocked)} for pg in m.pages]),
                    width="stretch", hide_index=True)

    state.nav_buttons("import", "score", next_disabled=False,
                      next_label="Tiếp tục ➜" if pages else "Bỏ qua (chỉ dùng mô tả UC) ➜")
