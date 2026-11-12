"""Thu số liệu tài nguyên máy chủ (CPU, RAM, I/O, kết nối CSDL) – tuỳ chọn.

render_config(): khối cấu hình ở bước 6 · render_data(): số liệu đã thu theo lượt chạy ở bước 8.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ...charts import charts as chartlib
from ...models import DB_TYPES, MONITOR_TOOLS, SERVER_ROLES, MonitorServer, Project
from ...monitor import METRICS, perfmon, sources, store
from ...monitor.runtime import fetch_history
from ...scriptgen.profile import scenario_name
from ...storage import save_password
from .. import state

ACCESS = {"ssh": "SSH (đọc trực tiếp khi chạy test)", "local": "Máy chạy test này (không cần SSH)",
          "file": "Tải file Performance Monitor lên"}


# ---------------------------------------------------------------- mật khẩu (phiên + Windows Credential Manager)

def secret(p: Project, key: str) -> str:
    store_ = st.session_state.setdefault(f"monsec_{p.id}", {})
    if key not in store_:
        from ...storage import load_password
        store_[key] = load_password(p.id, key)
    return store_.get(key, "")


def set_secret(p: Project, key: str, value: str, remember: bool) -> None:
    st.session_state.setdefault(f"monsec_{p.id}", {})[key] = value
    if remember and value:
        save_password(p.id, key, value)


def monitor_secrets(p: Project) -> dict[str, str]:
    """Mật khẩu/token cần cho tiến trình thu số liệu (truyền qua biến môi trường, không ghi ra file)."""
    m = p.monitor
    keys = [sources.ssh_secret_key(s) for s in m.servers if s.access == "ssh" and s.host]
    if m.db.enabled and m.db.host:
        keys.append(sources.db_secret_key(m.db))
    if "zabbix" in m.tools:
        keys.append(sources.ZABBIX_SECRET)
    if "prometheus" in m.tools:
        keys.append(sources.PROM_SECRET)
    return {k: secret(p, k) for k in keys}


def missing_secrets(p: Project) -> list[str]:
    """Máy chủ SSH / CSDL đang bật nhưng chưa có mật khẩu (và không có khoá SSH)."""
    m = p.monitor
    out = [s.name or s.host for s in m.servers
           if "perfmon" in m.tools and s.access == "ssh" and s.host and not s.ssh_key_file
           and not secret(p, sources.ssh_secret_key(s))]
    if m.db.enabled and m.db.host and not secret(p, sources.db_secret_key(m.db)):
        out.append(f"CSDL {m.db.host}")
    return out


def summary_text(p: Project) -> str:
    m = p.monitor
    tools = ", ".join(MONITOR_TOOLS[t] for t in m.tools if t in MONITOR_TOOLS) or "chưa chọn công cụ"
    db = f" + số kết nối {DB_TYPES.get(m.db.db_type)}" if m.db.enabled else ""
    return f"{tools}{db} · {len(m.servers)} máy chủ · mỗi {m.interval_s} giây"


# ---------------------------------------------------------------- giao diện

def _row_key(r: dict) -> str:
    return f"{r['run_id']}|{r['uc_code']}"


def _sub(text: str) -> None:
    """Tiêu đề mục con trong khối cấu hình: <mã mục>.1., .2.… (đánh số liên tục theo mục đang hiển thị)."""
    code, n = st.session_state["_mon_sub"]
    st.session_state["_mon_sub"] = (code, n + 1)
    st.markdown(f"#### {code}.{n + 1}. {text}")


def render_config(p: Project, code: str = "6b") -> None:
    """Bước 6: cấu hình thu số liệu tài nguyên máy chủ (không bắt buộc). code = chỉ mục của khối, vd "6b"."""
    st.divider()
    st.subheader(f"{code}. Thu số liệu tài nguyên máy chủ (tuỳ chọn)")
    st.session_state["_mon_sub"] = (code, 0)
    m = p.monitor
    st.caption("Phần này **không bắt buộc**. Khi bật, PerfTool thu CPU, RAM, ổ đĩa (I/O), mạng và số kết nối CSDL "
               "**trong lúc chạy test ở Bước 7**; số liệu xem ở Bước 8, biểu đồ ở Bước 9 và đưa vào báo cáo ở Bước 10. "
               "Mọi thao tác chỉ ĐỌC số liệu, không thay đổi gì trên máy chủ.")
    on = st.toggle("Thực hiện thu thập số liệu CPU, RAM, I/O, kết nối CSDL", m.enabled, key=f"mon_on_{p.id}")
    if on != m.enabled:
        m.enabled = on
        state.save(p)
        st.rerun()
    if not m.enabled:
        st.info("Đang tắt – báo cáo sẽ ghi “Số liệu tài nguyên máy chủ: không thu thập”. Bật công tắc trên để cấu hình.")
        return
    st.warning("⚠️ Số liệu chỉ có cho các lượt chạy **sau khi bật** (Performance Monitor đọc trực tiếp lúc test chạy). "
               "Lượt đã chạy trước đó chỉ lấy được qua Zabbix/Prometheus (có lưu lịch sử) hoặc file log tải lên.")

    _tools_section(p)
    _servers_section(p)
    if "zabbix" in m.tools:
        _zabbix_section(p)
    if "prometheus" in m.tools:
        _prom_section(p)
    _db_section(p)
    if "perfmon" in m.tools:
        _logman_guide()


def _tools_section(p: Project) -> None:
    m = p.monitor
    _sub("Công cụ giám sát")
    c1, c2 = st.columns([3, 1])
    tools = c1.multiselect("Chọn 1 hoặc nhiều công cụ đang có trên máy chủ", list(MONITOR_TOOLS), m.tools,
                           format_func=MONITOR_TOOLS.get, key=f"mon_tools_{p.id}")
    interval = c2.number_input("Lấy mẫu mỗi (giây)", 1, 300, int(m.interval_s), key=f"mon_int_{p.id}")
    if tools != m.tools or interval != m.interval_s:
        m.tools, m.interval_s = tools, int(interval)
        state.save(p)
    with st.expander("❔ Mỗi công cụ lấy số liệu thế nào"):
        st.markdown(
            "- **Windows Performance Monitor**: PerfTool chạy lệnh `typeperf` (có sẵn trên Windows) để đọc bộ đếm "
            "CPU/RAM/ổ đĩa/mạng mỗi vài giây trong lúc test chạy – qua **SSH** (máy chủ cần bật OpenSSH Server) hoặc "
            "trên chính máy chạy test. Không có SSH: quản trị viên tự ghi log (lệnh ở mục *Máy chủ không có SSH* bên dưới) rồi **tải file lên ở Bước 8**.\n"
            "- **Zabbix**: gọi API Zabbix lấy lịch sử các item (CPU, RAM, ổ đĩa) đúng khung giờ từng lượt chạy.\n"
            "- **Grafana / Prometheus**: gọi API `query_range` của Prometheus (hoặc qua Grafana datasource proxy) "
            "với các câu PromQL mặc định cho windows_exporter / node_exporter.\n"
            "- **Kết nối CSDL** (mục 3): chạy định kỳ 1 câu SELECT đếm số phiên kết nối – cần tài khoản CSDL chỉ đọc.")


def _servers_section(p: Project) -> None:
    m = p.monitor
    _sub("Máy chủ cần giám sát")
    st.caption("Thêm mỗi máy chủ 1 dòng (web, ứng dụng, CSDL…). Cột Zabbix/Prometheus chỉ cần khi dùng công cụ đó.")
    df = pd.DataFrame([{"Tên hiển thị": s.name, "Vai trò": s.role, "Địa chỉ (IP/tên máy)": s.host,
                        "Cách lấy Performance Monitor": s.access, "Cổng SSH": s.ssh_port,
                        "Tài khoản SSH": s.ssh_user, "Host Zabbix": s.zabbix_host,
                        "Instance Prometheus": s.prom_instance} for s in m.servers]
                      or [], columns=["Tên hiển thị", "Vai trò", "Địa chỉ (IP/tên máy)", "Cách lấy Performance Monitor",
                                      "Cổng SSH", "Tài khoản SSH", "Host Zabbix", "Instance Prometheus"])
    form = st.form(f"mon_srv_form_{p.id}", border=False)   # chỉ lưu khi bấm nút, không tải lại trang sau mỗi ô
    ed = form.data_editor(df, num_rows="dynamic", width="stretch", hide_index=True, key=f"mon_srv_{p.id}",
                        column_config={
                            "Vai trò": st.column_config.SelectboxColumn(options=list(SERVER_ROLES), default="web",
                                                                        required=True),
                            "Cách lấy Performance Monitor": st.column_config.SelectboxColumn(
                                options=list(ACCESS), default="ssh", required=True,
                                help="ssh: đọc qua SSH · local: chính máy chạy test · file: tải file log lên"),
                            "Cổng SSH": st.column_config.NumberColumn(min_value=1, max_value=65535, default=22),
                            # dòng mới: ô trống thay vì chữ "None"
                            **{c: st.column_config.TextColumn(default="") for c in
                               ("Tên hiển thị", "Địa chỉ (IP/tên máy)", "Tài khoản SSH", "Host Zabbix",
                                "Instance Prometheus")},
                        })
    form.caption("Vai trò: " + " · ".join(f"`{k}` {v}" for k, v in SERVER_ROLES.items())
                 + " — Cách lấy: " + " · ".join(f"`{k}` {v}" for k, v in ACCESS.items()))
    if form.form_submit_button("💾 Lưu danh sách máy chủ"):
        def txt(v) -> str:            # ô trống của data_editor có thể là None hoặc NaN (str(NaN) = "nan")
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

        by_host = {s.host: s for s in m.servers if s.host}
        by_name = {s.name: s for s in m.servers if s.name}
        new = []
        for r in ed.to_dict("records"):
            host = txt(r.get("Địa chỉ (IP/tên máy)"))
            name = txt(r.get("Tên hiển thị"))
            if not host and not name:
                continue
            # giữ các trường không hiện trên bảng (khoá SSH, tổng RAM đã đo) – khớp theo địa chỉ, không có thì theo tên
            prev = by_host.get(host) or by_name.get(name)
            new.append(MonitorServer(
                name=name or host, role=r.get("Vai trò") or "web", host=host,
                access=r.get("Cách lấy Performance Monitor") or "ssh",
                ssh_port=int(r.get("Cổng SSH") or 22) if pd.notna(r.get("Cổng SSH")) else 22,
                ssh_user=txt(r.get("Tài khoản SSH")),
                ssh_key_file=prev.ssh_key_file if prev else "", ram_total_mb=prev.ram_total_mb if prev else None,
                zabbix_host=txt(r.get("Host Zabbix")),
                prom_instance=txt(r.get("Instance Prometheus"))))
        no_ip = [s.name for s in new if s.access == "ssh" and not s.host]
        if no_ip:
            st.error(f"Máy chủ lấy số liệu qua SSH cần có địa chỉ IP/tên máy: {', '.join(no_ip)}.")
            return
        names = [s.name for s in new]
        if len(set(names)) != len(names):
            st.error("Tên hiển thị các máy chủ phải khác nhau.")
        else:
            m.servers = new
            state.save(p)
            st.success(f"Đã lưu {len(new)} máy chủ.")
            st.rerun()

    for i, s in enumerate(m.servers):
        if "perfmon" not in m.tools or s.access == "file":
            continue
        with st.expander(f"🔌 {s.name} ({s.host or '—'}) – {ACCESS[s.access]}"
                         + (f" · RAM {s.ram_total_mb / 1024:.1f} GB" if s.ram_total_mb else "")):
            if s.access == "ssh":
                key = sources.ssh_secret_key(s)
                c1, c2 = st.columns(2)
                pwd = c1.text_input(f"Mật khẩu SSH của {s.ssh_user or '(chưa nhập tài khoản)'}", secret(p, key),
                                    type="password", key=f"mon_pwd_{p.id}_{i}")
                kf = c2.text_input("Hoặc đường dẫn file khoá riêng (tuỳ chọn)", s.ssh_key_file, key=f"mon_kf_{p.id}_{i}",
                                   help="VD C:\\Users\\ban\\.ssh\\id_rsa – dùng thay cho mật khẩu")
                rem = st.checkbox("Ghi nhớ mật khẩu (Windows Credential Manager)", True, key=f"mon_rem_{p.id}_{i}")
                st.caption("Máy chủ Windows cần bật OpenSSH Server (Settings → Optional features → OpenSSH Server). "
                           "Tài khoản nên thuộc nhóm *Performance Monitor Users* – không cần quyền quản trị.")
            else:
                key, pwd, kf, rem = "", "", "", False
                st.caption("Đo chính máy đang chạy PerfTool – dùng để chứng minh máy tạo tải không bị quá tải "
                           "(khi đó kết quả đo mới phản ánh đúng máy chủ).")
            if st.button("🔍 Lưu & kiểm tra kết nối", key=f"mon_test_{p.id}_{i}"):
                if key:
                    set_secret(p, key, pwd, rem)
                s.ssh_key_file = kf.strip()
                try:
                    with st.spinner(f"Đang đọc thử bộ đếm hiệu năng trên {s.host or s.name}..."):
                        vals, ram = sources.test_server(s, pwd)
                    if ram:
                        s.ram_total_mb = ram
                    state.save(p)
                    st.success("Kết nối thành công – " + ", ".join(
                        f"{METRICS[k][0]} {store.fmt_val(v, METRICS[k][1])}" for k, v in vals.items())
                        + (f" · tổng RAM {ram / 1024:.1f} GB" if ram else ""))
                    fp = sources.host_fingerprint(s) if s.access == "ssh" else ""
                    if fp:
                        st.caption(f"🔐 Khoá máy chủ đã tin cậy: `{fp}` – đối chiếu với quản trị máy chủ nếu kết nối "
                                   "lần đầu. Các lần thu số liệu sau chỉ kết nối tới máy có đúng khoá này.")
                except Exception as e:  # noqa: BLE001
                    state.save(p)
                    st.error(f"Không đọc được số liệu: {e}")


def _zabbix_section(p: Project) -> None:
    m = p.monitor
    _sub("Zabbix")
    c1, c2, c3 = st.columns([2, 1, 1])
    url = c1.text_input("Địa chỉ Zabbix", m.zabbix_url, placeholder="http://zabbix.local/zabbix", key=f"zb_url_{p.id}")
    user = c2.text_input("Tài khoản (để trống nếu dùng API token)", m.zabbix_user, key=f"zb_user_{p.id}")
    pwd = c3.text_input("Mật khẩu / API token", secret(p, sources.ZABBIX_SECRET), type="password", key=f"zb_pwd_{p.id}")
    items = sources.zabbix_items(m)
    txt = st.text_area("Item key cho từng chỉ số (mỗi dòng `chỉ số = item key`; giá trị byte/s tự đổi sang MB/s)",
                       "\n".join(f"{k} = {v}" for k, v in items.items()), height=130, key=f"zb_items_{p.id}")
    if st.button("💾 Lưu & kiểm tra Zabbix", key=f"zb_test_{p.id}"):
        m.zabbix_url, m.zabbix_user = url.strip(), user.strip()
        m.zabbix_items = _parse_kv(txt)
        set_secret(p, sources.ZABBIX_SECRET, pwd, True)
        state.save(p)
        try:
            z = sources.Zabbix(m.zabbix_url, m.zabbix_user, pwd)
            found = [s.zabbix_host for s in m.servers if s.zabbix_host and _safe_host(z, s.zabbix_host)]
            st.success(f"Kết nối Zabbix {z.version()} thành công – tìm thấy {len(found)}/"
                       f"{len([s for s in m.servers if s.zabbix_host])} host.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Lỗi Zabbix: {e}")


def _safe_host(z, name: str) -> bool:
    try:
        z.host_id(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _prom_section(p: Project) -> None:
    m = p.monitor
    _sub("Grafana / Prometheus")
    c1, c2 = st.columns([2, 1])
    url = c1.text_input("Địa chỉ Prometheus hoặc Grafana datasource proxy", m.prom_url,
                        placeholder="http://prometheus:9090  hoặc  https://grafana/api/datasources/proxy/uid/<uid>",
                        key=f"pm_url_{p.id}")
    tok = c2.text_input("Token (Grafana service account, tuỳ chọn)", secret(p, sources.PROM_SECRET), type="password",
                        key=f"pm_tok_{p.id}")
    preset = st.radio("Bộ câu truy vấn mẫu", list(sources.PROM_PRESETS), horizontal=True, key=f"pm_preset_{p.id}",
                      help="windows_exporter cho máy chủ Windows, node_exporter cho Linux")
    queries = {**sources.PROM_PRESETS[preset], **m.prom_queries} if m.prom_queries else sources.PROM_PRESETS[preset]
    txt = st.text_area("PromQL cho từng chỉ số (`$instance` được thay bằng cột Instance Prometheus của máy chủ)",
                       "\n".join(f"{k} = {v}" for k, v in queries.items()), height=160, key=f"pm_q_{p.id}_{preset}")
    if st.button("💾 Lưu & kiểm tra Prometheus", key=f"pm_test_{p.id}"):
        m.prom_url, m.prom_queries = url.strip(), _parse_kv(txt)
        set_secret(p, sources.PROM_SECRET, tok, True)
        state.save(p)
        try:
            st.success(f"Kết nối thành công – {sources.prom_test(m.prom_url, tok)}.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Lỗi Prometheus: {e}")


def _parse_kv(txt: str) -> dict[str, str]:
    out = {}
    for line in txt.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in METRICS:
                out[k.strip()] = v.strip()
    return out


def _db_section(p: Project) -> None:
    db = p.monitor.db
    _sub("Kết nối cơ sở dữ liệu")
    en = st.checkbox("Thu số kết nối CSDL (chạy câu truy vấn mỗi lần lấy mẫu)", db.enabled, key=f"db_on_{p.id}")
    if en != db.enabled:
        db.enabled = en
        state.save(p)
        st.rerun()
    if not db.enabled:
        return
    c1, c2, c3, c4 = st.columns([1.2, 2, 0.8, 1.5])
    typ = c1.selectbox("Loại CSDL", list(DB_TYPES), list(DB_TYPES).index(db.db_type), format_func=DB_TYPES.get,
                       key=f"db_type_{p.id}")
    host = c2.text_input("Máy chủ CSDL (IP/tên máy)", db.host, key=f"db_host_{p.id}")
    port = c3.number_input("Cổng", 0, 65535, int(db.port or sources.DEFAULT_PORTS[typ]), key=f"db_port_{p.id}_{typ}")
    dbname = c4.text_input("Tên CSDL" + (" (service name)" if typ == "oracle" else ""), db.database,
                           key=f"db_name_{p.id}")
    c1, c2, c3 = st.columns([1.5, 1.5, 1])
    user = c1.text_input("Tài khoản CSDL (chỉ đọc)", db.username, key=f"db_user_{p.id}")
    tmp = db.model_copy(update={"db_type": typ, "host": host.strip(), "username": user.strip()})
    pwd = c2.text_input("Mật khẩu CSDL", secret(p, sources.db_secret_key(tmp)), type="password", key=f"db_pwd_{p.id}")
    rem = c3.checkbox("Ghi nhớ mật khẩu", True, key=f"db_rem_{p.id}")
    default_q = sources.DEFAULT_QUERIES[typ]
    q = st.text_area(f"Câu truy vấn đếm số kết nối ({DB_TYPES[typ]}) – trả về 1 số",
                     db.query if db.query and db.db_type == typ else default_q, height=80, key=f"db_q_{p.id}_{typ}",
                     help="Chỉ cho phép 1 câu SELECT/WITH/SHOW. Mặc định: " + default_q)
    st.caption("Quyền cần có – SQL Server: VIEW SERVER STATE · PostgreSQL: pg_monitor · MySQL: PROCESS · "
               "Oracle: SELECT trên V$SESSION.")
    if st.button("💾 Lưu & chạy thử truy vấn", key=f"db_test_{p.id}"):
        db.db_type, db.host, db.port, db.database, db.username = typ, host.strip(), int(port), dbname.strip(), user.strip()
        db.query = "" if q.strip() == default_q else q.strip()
        set_secret(p, sources.db_secret_key(db), pwd, rem)
        state.save(p)
        try:
            with st.spinner("Đang kết nối CSDL..."):
                v = sources.test_db(db, pwd)
            st.success(f"Truy vấn thành công – hiện có **{v:.0f}** kết nối.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Lỗi CSDL: {e}")


def _logman_guide() -> None:
    """Bước 6: máy chủ không có SSH -> quản trị viên tự ghi log trong lúc test, sau đó tải file lên ở bước 8."""
    _sub("Máy chủ không có SSH – ghi log Performance Monitor")
    st.caption("Với máy chủ chọn cách lấy `file`: nhờ quản trị viên chạy các lệnh dưới đây **trước khi chạy test**, "
               "sau khi test xong thì **tải file log lên ở Bước 8** (mục Tài nguyên máy chủ theo lượt chạy).")
    with st.expander("📋 Lệnh cho quản trị viên máy chủ – ghi log Performance Monitor trong lúc test"):
        st.markdown("Chạy trên máy chủ (PowerShell/CMD quyền quản trị) **một lần** để tạo bộ ghi log:")
        st.code(perfmon.LOGMAN_CREATE, language="bat")
        st.markdown("Trước khi bấm Chạy test ở Bước 7:")
        st.code(perfmon.LOGMAN_START, language="bat")
        st.markdown("Sau khi test xong – lấy file `.csv` trong `C:\\PerfLogs\\PerfTool*` rồi tải lên ở Bước 8:")
        st.code(perfmon.LOGMAN_STOP, language="bat")
        st.caption("Cũng nhận file .blg (Performance Monitor → Save Data As) – PerfTool tự chuyển bằng relog. "
                   "Giờ trong file là giờ máy chủ: cần đồng bộ giờ máy chủ với máy chạy test để khớp khung giờ.")


def _upload_section(p: Project) -> None:
    """Bước 8: tải file log Performance Monitor (ghi trên máy chủ không có SSH) lên và nhập số liệu."""
    m = p.monitor
    if not m.servers:
        st.info("Chưa khai báo máy chủ – khai báo ở Bước 6 trước khi tải file log lên.")
        return
    c1, c2 = st.columns([2, 1])
    up = c1.file_uploader("File Performance Monitor (.csv / .blg)", type=["csv", "blg"], key=f"mon_up_{p.id}")
    names = [s.name for s in m.servers]
    sv = c2.selectbox("Thuộc máy chủ", names, key=f"mon_up_sv_{p.id}")
    if up and st.button("📥 Nhập số liệu", key=f"mon_up_btn_{p.id}"):
        server = next(s for s in m.servers if s.name == sv)
        tmpdir = Path(tempfile.mkdtemp(prefix="perftool_"))
        path = tmpdir / Path(up.name).name
        path.write_bytes(up.getvalue())
        try:
            rows, hosts = perfmon.parse_file(path, server.ram_total_mb)
            if not rows:
                st.error("File không có mẫu số liệu nào.")
            else:
                tag = f"{datetime.now():%Y%m%d-%H%M%S}_{Path(up.name).stem}"[:80]
                store.import_rows(p.id, server.name, rows, "file", tag)
                t0, t1 = min(r[0] for r in rows), max(r[0] for r in rows)
                st.session_state[f"mon_up_msg_{p.id}"] = (
                    f"Đã nhập {len(rows)} giá trị ({t0:%d/%m %H:%M:%S} → {t1:%d/%m %H:%M:%S}) cho {server.name}"
                    + (f" – file ghi từ máy {', '.join(hosts)}" if hosts else "") + ".")
        except Exception as e:  # noqa: BLE001
            st.error(f"Không đọc được file: {e}")
        finally:
            for f in tmpdir.iterdir():
                f.unlink(missing_ok=True)
            tmpdir.rmdir()
        if st.session_state.get(f"mon_up_msg_{p.id}"):
            st.rerun()
    if st.session_state.get(f"mon_up_msg_{p.id}"):
        st.success(st.session_state.pop(f"mon_up_msg_{p.id}"))


def render_data(p: Project, rows: list[dict], sec=None) -> None:
    """Bước 8: số liệu tài nguyên máy chủ đã thu của các kịch bản đang chọn."""
    mon = store.load_all(p.id)
    if not p.monitor.enabled and mon.empty:
        return
    (sec or st.subheader)("Tài nguyên máy chủ theo lượt chạy")
    if not p.monitor.enabled:
        st.caption("Thu số liệu máy chủ đang tắt (bật ở Bước 6) – dưới đây là số liệu đã thu trước đó.")
    table = []
    for r in rows:
        sl = store.run_slice(mon, r.get("started_at", ""), r.get("finished_at", ""))
        table.append({"Có số liệu": "✅" if not sl.empty else "—", "Mã UC": r["uc_code"], "Công cụ": r["tool"],
                      "Kịch bản": scenario_name(r["scenario_type"]),
                      "Khung giờ": f"{r.get('started_at', '')} → {r.get('finished_at', '')[11:]}",
                      "Máy chủ": ", ".join(dict.fromkeys(sl["server"])) if not sl.empty else "",
                      "Số giá trị": len(sl)})
    st.caption(f"Theo {len(rows)} kịch bản đã chọn ở trên. Cấu hình máy chủ / công cụ giám sát ở Bước 6.")
    st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
    m = p.monitor
    if {"zabbix", "prometheus"} & set(m.tools):
        if st.button("🔄 Lấy lại số liệu Zabbix/Prometheus cho các lượt trên", key=f"mon_refetch_{p.id}"):
            secrets = monitor_secrets(p)
            msgs: list[str] = []
            with st.spinner("Đang lấy lịch sử..."):
                for run_id in dict.fromkeys(r["run_id"] for r in rows):
                    r = next(x for x in rows if x["run_id"] == run_id)
                    fetch_history(p, r.get("started_at", ""), r.get("finished_at", ""), run_id, secrets, msgs.append)
            st.session_state[f"mon_msg_{p.id}"] = "\n".join(f"- {x}" for x in msgs) or "Không có gì để lấy."
            st.rerun()
        if st.session_state.get(f"mon_msg_{p.id}"):
            st.info(st.session_state.pop(f"mon_msg_{p.id}"))
    if m.enabled and "perfmon" in m.tools:
        has_file = any(s_.access == "file" for s_ in m.servers)
        with st.expander("📥 Tải file Performance Monitor lên (máy chủ không có SSH)",
                         expanded=has_file or bool(st.session_state.get(f"mon_up_msg_{p.id}"))):
            _upload_section(p)

    have = [r for r, t in zip(rows, table) if t["Số giá trị"]]
    if have:
        opts = {_row_key(r): f"{r['uc_code']} · {r['tool']} · {scenario_name(r['scenario_type'])} · {r['run_id'][:15]}"
                for r in have}
        sel = st.selectbox("Xem số liệu của lượt", list(opts), format_func=opts.get, key=f"mon_view_{p.id}")
        r = next(x for x in have if _row_key(x) == sel)
        sl = store.run_slice(mon, r.get("started_at", ""), r.get("finished_at", ""))
        summ = store.summarize(sl)
        st.dataframe(pd.DataFrame([{"Máy chủ": x["server"], "Chỉ số": f"{x['label']} ({x['unit']})",
                                    "Trung bình": store.fmt_val(x["avg"], x["unit"]),
                                    "Cao nhất": store.fmt_val(x["max"], x["unit"]), "Số mẫu": x["n"]} for x in summ]),
                     width="stretch", hide_index=True)
        png = chartlib.resource_chart(sl, f"{r['uc_code']} — {scenario_name(r['scenario_type'])}",
                                      store.monitor_dir(p.id) / "preview" / f"{r['run_id']}.png")
        if png:
            st.image(str(png), width="stretch")
        for line in store.insights(summ):
            st.markdown(f"- {line}")

    files = store.data_files(p.id)
    if files:
        with st.expander(f"🗂 File số liệu đã lưu ({len(files)})"):
            for f in files:
                c1, c2 = st.columns([5, 1])
                c1.caption(f"`{f.name}` · {f.stat().st_size / 1024:.0f} KB")
                if c2.button("🗑 Xoá", key=f"mon_del_{p.id}_{f.name}"):
                    f.unlink(missing_ok=True)
                    st.rerun()
