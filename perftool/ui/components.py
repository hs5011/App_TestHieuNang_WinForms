"""Thành phần giao diện dùng lại: tiêu đề mục đánh chỉ mục, bảng theo dõi tác vụ nền."""
from __future__ import annotations

import streamlit as st

from .. import jobs

def section_titles(step: int):
    """Tiêu đề các mục trong 1 bước, đánh chỉ mục thống nhất <bước><a, b, c…> theo thứ tự hiển thị (vd 3a., 3b.).

    Trả về hàm title(text, target=None) -> mã mục (vd "6b") để đánh chỉ mục cấp con "6b.1.", "6b.2."…
    Mục chỉ hiện khi đủ điều kiện vẫn được đánh chỉ mục liên tục, không bị nhảy chữ cái.
    """
    letters = iter("abcdefghijklmnopqrstuvwxyz")

    def title(text: str, target=None) -> str:
        code = f"{step}{next(letters)}"
        (target or st).subheader(f"{code}. {text}")
        return code
    return title


def check_all_buttons(key: str, n: int, on: str = "☑ Chọn tất cả", off: str = "☐ Bỏ chọn tất cả",
                      help: str | None = None) -> bool | None:
    """Cặp nút tick / bỏ tick tất cả, đặt ngay TRÊN bảng có cột tick. Trả về True/False khi bấm, None nếu không bấm."""
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button(on, key=f"{key}_all", width="stretch", disabled=n == 0, help=help):
        return True
    if c2.button(off, key=f"{key}_none", width="stretch", disabled=n == 0, help=help):
        return False
    return None


STATUS_VI = {"running": "⏳ Đang chạy", "done": "✅ Đã chạy xong", "failed": "❌ Lỗi", "stopped": "⏹ Đã dừng"}


def _status_line(s: dict, alive: bool) -> str:
    """'⏳ Đang chạy · 2/5 · bắt đầu ... · kết thúc ...'"""
    parts = [STATUS_VI.get(s.get("status"), s.get("status") or "")]
    if s.get("progress") and alive:
        parts.append(str(s["progress"]))
    if s.get("started"):
        parts.append(f"bắt đầu {s['started']}")
    if s.get("finished"):
        parts.append(f"kết thúc {s['finished']}")
    return " · ".join(parts)


def _log_newest_first(project_id: str, job: str, tail: int = 400) -> str:
    """Log của job, dòng mới nhất nằm trên cùng."""
    txt = jobs.read_log(project_id, job, tail)
    return "\n".join(reversed(txt.splitlines())) if txt else "(chưa có log)"


def job_panel(project_id: str, job: str, title: str, height: int = 260) -> dict | None:
    """Khung theo dõi tác vụ nền (dùng chung mọi bước): nhóm thu gọn/mở rộng, tiêu đề ghi trạng thái
    (tiến độ, giờ bắt đầu/kết thúc); bên trong có nút Dừng và log (dòng mới nhất trên cùng).
    Tự làm mới mỗi 2 giây khi đang chạy. Trả về status dict."""
    st_ = jobs.read_status(project_id, job)
    if not st_:
        return None
    running = jobs.is_running(project_id, job)

    @st.fragment(run_every=2 if running else None)
    def _panel():
        s = jobs.read_status(project_id, job) or {}
        alive = jobs.is_running(project_id, job)
        # key gắn giờ bắt đầu: mỗi lần chạy mới là 1 khung mới -> tự mở ra, không nhớ trạng thái thu gọn của lần trước
        with st.expander(f"📜 {title} – {_status_line(s, alive)}", expanded=running,
                         key=f"jobexp_{project_id}_{job}_{s.get('started', '')}"):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            if s.get("message") and s.get("status") != "running":
                (c1.error if s.get("status") == "failed" else c1.caption)(s["message"])
            elif alive:
                c1.caption("Log tự cập nhật mỗi 2 giây · dòng mới nhất ở trên cùng.")
            else:
                c1.caption("Dòng mới nhất ở trên cùng.")
            if alive and c2.button("⏹ Dừng", key=f"stop_{job}", width="stretch"):
                jobs.stop_job(project_id, job)
                st.rerun(scope="app")
            st.code(_log_newest_first(project_id, job), language="log", height=height)
        if not alive and running:
            # vừa chuyển từ chạy -> kết thúc: làm mới toàn trang để nạp kết quả
            st.rerun(scope="app")

    _panel()
    return jobs.read_status(project_id, job)
