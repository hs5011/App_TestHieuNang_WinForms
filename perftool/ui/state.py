"""Trạng thái phiên giao diện & tiện ích dùng chung cho các bước."""
from __future__ import annotations

from typing import Optional

import streamlit as st

from ..models import Project
from ..storage import load_password, load_project, save_project

STEPS = [
    ("info", "1. Nhập thông tin"),
    ("import", "2. Import Use Case"),
    ("crawl", "3. Đăng nhập & phân tích Web"),
    ("score", "4. Phân tích & chọn UC"),
    ("confirm", "5. Xác nhận"),
    ("tool", "6. Chọn công cụ"),
    ("run", "7. Cấu hình & chạy test"),
    ("results", "8. Kết quả & phân tích"),
    ("charts", "9. Biểu đồ"),
    ("report", "10. Xuất báo cáo"),
]


def project() -> Optional[Project]:
    pid = st.session_state.get("project_id")
    if not pid:
        return None
    p: Optional[Project] = st.session_state.get("_project")
    # sau khi mã nguồn được nạp lại (file watcher), đối tượng cũ trong phiên thuộc lớp Project cũ (thiếu trường mới)
    # -> nạp lại từ file (mọi thay đổi đều đã được lưu ngay khi thao tác nên không mất dữ liệu)
    if p is None or p.id != pid or type(p) is not Project:
        p = load_project(pid)
        st.session_state["_project"] = p
    return p


def reload() -> Project:
    st.session_state["_project"] = load_project(st.session_state["project_id"])
    return st.session_state["_project"]


def save(p: Project) -> None:
    save_project(p)
    st.session_state["_project"] = p


def password(p: Project) -> str:
    key = f"pwd_{p.id}"
    if not st.session_state.get(key) and p.login.username:
        st.session_state[key] = load_password(p.id, p.login.username)
    return st.session_state.get(key, "")


def set_password(p: Project, pwd: str) -> None:
    st.session_state[f"pwd_{p.id}"] = pwd


def account_password(p: Project, username: str) -> str:
    """Mật khẩu của 1 tài khoản bổ sung: ưu tiên trong phiên, sau đó Windows Credential Manager."""
    store = st.session_state.setdefault(f"accpwd_{p.id}", {})
    if username not in store:
        store[username] = load_password(p.id, username)
    return store.get(username, "")


def set_account_password(p: Project, username: str, pwd: str) -> None:
    st.session_state.setdefault(f"accpwd_{p.id}", {})[username] = pwd


def test_accounts(p: Project, mode: str | None = None) -> list[tuple[str, str]]:
    """Danh sách (tài khoản, mật khẩu) dùng khi chạy test. Phần tử đầu luôn là tài khoản dự phòng/chính."""
    main = [(p.login.username, password(p))] if p.login.username else []
    if (mode or p.test_config.account_mode) == "single":
        return main
    extras = [(u, account_password(p, u)) for u in p.login.extra_accounts if u != p.login.username]
    pool = (main if p.login.include_main_in_pool else []) + extras
    return pool or main


def goto(step_key: str) -> None:
    st.session_state["step"] = step_key
    st.rerun()


def step_done(p: Project, key: str) -> bool:
    return {
        "info": bool(p.login.base_url and p.login.username),
        "import": bool(p.use_cases),
        "crawl": any(m.pages for m in p.modules),
        "score": bool(p.scores),
        "confirm": p.confirmed,
        # bước 6 có sẵn công cụ mặc định (k6) -> chỉ tính xong khi đã mở bước 6 trong phiên hoặc đã sinh script
        "tool": p.confirmed and bool(p.test_config.tools) and (bool(p.runs) or st.session_state.get(f"visited_tool_{p.id}", False)),
        "run": any(r.status == "done" for r in p.runs),
        "results": any(r.status == "done" for r in p.runs),
        "charts": any(r.status == "done" for r in p.runs),
        "report": _has_report(p),
    }.get(key, False)


def _has_report(p: Project) -> bool:
    from ..storage import PROJECTS_DIR
    d = PROJECTS_DIR / p.id / "reports"
    return d.is_dir() and any(d.glob("*.docx"))


def nav_buttons(prev_key: Optional[str], next_key: Optional[str], next_disabled: bool = False,
                next_label: str = "Tiếp tục ➜") -> None:
    st.divider()
    c1, _, c3 = st.columns([1, 3, 1])
    if prev_key and c1.button("⬅ Quay lại", width="stretch", key=f"prev_{prev_key}"):
        goto(prev_key)
    if next_key and c3.button(next_label, type="primary", width="stretch", disabled=next_disabled,
                              key=f"next_{next_key}"):
        goto(next_key)
