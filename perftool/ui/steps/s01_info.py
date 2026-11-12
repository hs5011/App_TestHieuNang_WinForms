"""Bước 1 - Nhập thông tin hệ thống & tài khoản."""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from ... import accounts as acc
from ...models import Project
from ...storage import delete_password, save_password
from .. import state

SAMPLE_CSV = "username,password\nuser01,MatKhau@01\nuser02,MatKhau@02\n"


def render(p: Project) -> None:
    st.header("1. Nhập thông tin hệ thống")
    st.caption("Khai báo URL, tài khoản đăng nhập và thông tin dự án. Mật khẩu không lưu vào file dự án.")

    kind = st.radio("Loại ứng dụng cần kiểm thử", ["web", "winforms"], horizontal=True, key=f"kind_{p.id}",
                    index=0 if p.kind != "winforms" else 1,
                    format_func=lambda k: {"web": "🌐 Ứng dụng Web (trình duyệt)",
                                           "winforms": "🪟 Ứng dụng WinForms / desktop (gọi API máy chủ)"}[k],
                    help="WinForms: PerfTool không điều khiển giao diện desktop mà ghi lại các lời gọi API của ứng dụng "
                         "(qua proxy ghi hoặc file HAR) rồi sinh kịch bản k6/JMeter phát lại tải lên máy chủ API.")
    if kind != p.kind:
        p.kind = kind
        state.save(p)
        st.rerun()
    win = p.kind == "winforms"
    if win:
        st.info("Chế độ **WinForms**: nhập **URL máy chủ API** mà ứng dụng desktop gọi tới (vd https://api.congty.vn). "
                "Tài khoản dùng để đăng nhập API khi chạy test. Các ô selector/trình duyệt không dùng ở chế độ này.")

    with st.form("info_form"):
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("1a. Thông tin dự án")
            name = st.text_input("Tên dự án *", p.info.name)
            system = st.text_input("Tên hệ thống", p.info.system_name, placeholder="VD: Hệ thống điều hành nội bộ")
            env = st.selectbox("Môi trường kiểm thử", ["staging", "uat", "test", "production", "dev"],
                               index=["staging", "uat", "test", "production", "dev"].index(p.info.environment)
                               if p.info.environment in ["staging", "uat", "test", "production", "dev"] else 0)
            author = st.text_input("Người thực hiện", p.info.author)
            org = st.text_input("Đơn vị", p.info.organization)
        with c2:
            st.subheader("1b. Hệ thống & tài khoản")
            base = st.text_input("URL máy chủ API *" if win else "URL hệ thống *", p.login.base_url,
                                 placeholder="https://api.example.gov.vn" if win else "https://example.gov.vn")
            login_url = st.text_input("URL API đăng nhập (nếu khác)" if win else "URL trang đăng nhập (nếu khác)", p.login.login_url)
            user = st.text_input("Tên đăng nhập *", p.login.username)
            pwd = st.text_input("Mật khẩu *", state.password(p), type="password")
            remember = st.checkbox("Ghi nhớ mật khẩu (Windows Credential Manager)", value=bool(state.password(p)))

        with st.expander("Tuỳ chọn đăng nhập nâng cao (selector, OTP/CAPTCHA)"):
            a1, a2 = st.columns(2)
            us = a1.text_input("CSS selector ô tài khoản", p.login.username_selector, placeholder="tự dò nếu để trống")
            ps = a1.text_input("CSS selector ô mật khẩu", p.login.password_selector, placeholder="input[type=password]")
            ss = a2.text_input("CSS selector nút đăng nhập", p.login.submit_selector, placeholder="button[type=submit]")
            ok_url = a2.text_input("URL sau đăng nhập chứa chuỗi", p.login.success_url_contains,
                                   placeholder="VD: /dashboard")
            manual = st.checkbox("Tôi sẽ tự đăng nhập trên trình duyệt (khi có OTP/CAPTCHA/SSO)", p.login.manual_login)
            headless = st.checkbox("Chạy trình duyệt ẩn (headless)", p.login.headless)
            insecure = st.checkbox("Bỏ qua kiểm tra chứng chỉ HTTPS (chỉ máy chủ kiểm thử dùng chứng chỉ tự ký)",
                                   p.login.skip_tls_verify,
                                   help="Khi bật, crawler, nút Kiểm tra đăng nhập và script k6 chấp nhận mọi chứng chỉ – "
                                        "mật khẩu có thể bị gửi tới máy chủ giả mạo. Chỉ bật trong mạng nội bộ tin cậy.")
            if insecure:
                st.warning("Đang tắt kiểm tra chứng chỉ HTTPS: mật khẩu kiểm thử có thể bị gửi tới máy chủ giả mạo.")

        submitted = st.form_submit_button("💾 Lưu thông tin", type="primary")

    if submitted:
        errs = []
        if not name.strip():
            errs.append("Tên dự án")
        if not base.strip().startswith(("http://", "https://")):
            errs.append("URL hệ thống (bắt đầu bằng http:// hoặc https://)")
        if not user.strip():
            errs.append("Tên đăng nhập")
        if not pwd and not manual:
            errs.append("Mật khẩu")
        if errs:
            st.error("Vui lòng nhập: " + ", ".join(errs))
        else:
            p.info.name, p.info.system_name, p.info.environment = name.strip(), system.strip(), env
            p.info.author, p.info.organization = author.strip(), org.strip()
            p.login.base_url, p.login.login_url = base.strip().rstrip("/"), login_url.strip()
            p.login.username = user.strip()
            p.login.username_selector, p.login.password_selector = us.strip(), ps.strip()
            p.login.submit_selector, p.login.success_url_contains = ss.strip(), ok_url.strip()
            p.login.manual_login, p.login.headless, p.login.skip_tls_verify = manual, headless, insecure
            state.set_password(p, pwd)
            if remember and pwd:
                if not save_password(p.id, p.login.username, pwd):
                    st.warning("Không lưu được mật khẩu vào Credential Manager; mật khẩu chỉ giữ trong phiên.")
            state.save(p)
            st.success("Đã lưu thông tin.")

    accounts_section(p)
    state.nav_buttons(None, "import", next_disabled=not state.step_done(p, "info"))


def accounts_section(p: Project) -> None:
    """Danh sách tài khoản kiểm thử – dùng để chia cho các người dùng ảo ở bước 7."""
    st.divider()
    st.subheader("1c. Tài khoản kiểm thử (tuỳ chọn – nhiều tài khoản)")
    st.caption("Khi chạy test, mỗi người dùng ảo (VU) sẽ đăng nhập bằng một tài khoản trong danh sách này, chia đều "
               "theo vòng (VU 1 → tài khoản 1, VU 2 → tài khoản 2, …). Dùng nhiều tài khoản giúp mô phỏng đúng thực tế "
               "và tránh bị giới hạn tần suất (HTTP 429) khi mọi VU dùng chung 1 tài khoản. Tài khoản chính ở trên vẫn "
               "được dùng để đăng nhập & quét web ở bước 3. Mật khẩu không bao giờ hiển thị lại và không lưu vào file dự án.")

    with st.form("acc_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 2])
        text = c1.text_area("Dán danh sách – mỗi dòng: tài_khoản,mật_khẩu", height=130,
                            placeholder="user01,MatKhau@01\nuser02,MatKhau@02\n(chấp nhận dấu phẩy, chấm phẩy hoặc tab)")
        up = c2.file_uploader("hoặc tải file CSV / Excel (cột username, password)", type=["csv", "txt", "xlsx", "xls"])
        remember = c2.checkbox("Ghi nhớ mật khẩu (Windows Credential Manager)", True)
        submitted = st.form_submit_button("➕ Thêm / cập nhật tài khoản", type="primary")
    st.download_button("⬇ Tải file mẫu", SAMPLE_CSV, file_name="tai_khoan_mau.csv", mime="text/csv")

    if submitted:
        try:
            new = acc.parse_text(text)
            if up is not None:
                new += acc.parse_file(up.getvalue(), up.name)
        except Exception as e:  # noqa: BLE001
            st.error(f"Không đọc được danh sách: {e}")
            new = []
        new = [(u, pw) for u, pw in dict(new).items() if u != p.login.username]
        if not new:
            st.warning("Không có tài khoản hợp lệ nào (tài khoản chính ở trên không cần nhập lại).")
        else:
            missing = [u for u, pw in new if not pw]
            for u, pw in new:
                if u not in p.login.extra_accounts:
                    p.login.extra_accounts.append(u)
                if pw:
                    state.set_account_password(p, u, pw)
                    if remember:
                        save_password(p.id, u, pw)
            state.save(p)
            st.session_state.pop(f"acccheck_{p.id}", None)
            st.success(f"Đã thêm/cập nhật {len(new)} tài khoản.")
            if missing:
                st.warning(f"{len(missing)} tài khoản chưa có mật khẩu: {', '.join(missing[:10])}"
                           + ("…" if len(missing) > 10 else ""))

    extras = [u for u in p.login.extra_accounts if u != p.login.username]
    inc = st.checkbox("Tài khoản chính cũng tham gia chạy test", p.login.include_main_in_pool,
                      help="Bỏ chọn nếu muốn dành tài khoản chính cho việc quét web, chỉ dùng tài khoản bổ sung để chạy tải.")
    if inc != p.login.include_main_in_pool:
        p.login.include_main_in_pool = inc
        state.save(p)

    pool = state.test_accounts(p, mode="multi")
    checks: dict = st.session_state.get(f"acccheck_{p.id}", {})
    rows = [{"STT": i, "Tên đăng nhập": u,
             "Loại": "Tài khoản chính" if u == p.login.username else "Bổ sung",
             "Mật khẩu": "✅ đã có" if pw else "⚠️ chưa có",
             "Kiểm tra đăng nhập": (("✅ " if checks[u]["ok"] else "❌ ") + checks[u]["message"]
                                    + (f" ({checks[u]['ms']} ms)" if checks[u].get("ms") else "")) if u in checks else ""}
            for i, (u, pw) in enumerate(pool, 1)]
    m1, m2, m3 = st.columns(3)
    m1.metric("Tài khoản dùng khi chạy test", len(pool))
    m2.metric("Thiếu mật khẩu", sum(1 for _, pw in pool if not pw))
    if checks:
        m3.metric("Đăng nhập thành công", f"{sum(1 for c in checks.values() if c['ok'])}/{len(checks)}")
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=min(38 * len(rows) + 40, 360))

    b1, b3 = st.columns([2, 3])
    can_check = bool(p.auth.login_url and p.auth.login_body_template)
    if b1.button("🔑 Kiểm tra đăng nhập", disabled=not pool or not can_check, width="stretch",
                 help="Gửi 1 request đăng nhập cho từng tài khoản (qua API đăng nhập đã nhận diện ở bước 3)."
                 if can_check else "Cần chạy bước 3 (Đăng nhập & phát hiện phân hệ) trước để nhận diện API đăng nhập."):
        res = {}
        bar = st.progress(0.0, text="Đang kiểm tra...")
        for i, (u, pw) in enumerate(pool, 1):
            res[u] = acc.check_login(p.auth, u, pw, insecure=p.login.skip_tls_verify) if pw else {"ok": False, "ms": None, "message": "Chưa có mật khẩu"}
            bar.progress(i / len(pool), text=f"Đang kiểm tra {i}/{len(pool)}: {u}")
            time.sleep(0.3)          # giãn cách để không tự gây giới hạn tần suất
        st.session_state[f"acccheck_{p.id}"] = res
        st.rerun()
    if not can_check:
        b3.caption("Nút kiểm tra đăng nhập dùng được sau khi chạy bước 3.")
    if extras:
        with st.expander(f"🗑 Xoá tài khoản bổ sung ({len(extras)})"):
            rm = st.multiselect("Chọn tài khoản cần xoá", extras)
            d1, d2 = st.columns(2)
            if d1.button("Xoá tài khoản đã chọn", disabled=not rm):
                _remove(p, rm)
            if d2.button("Xoá tất cả tài khoản bổ sung"):
                _remove(p, extras)


def _remove(p: Project, users: list[str]) -> None:
    for u in users:
        delete_password(p.id, u)
        st.session_state.get(f"accpwd_{p.id}", {}).pop(u, None)
    p.login.extra_accounts = [u for u in p.login.extra_accounts if u not in users]
    state.save(p)
    st.session_state.pop(f"acccheck_{p.id}", None)
    st.rerun()
