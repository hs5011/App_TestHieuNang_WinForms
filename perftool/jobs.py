"""Quản lý tác vụ nền (crawler, chạy test) dưới dạng tiến trình con.

Chạy tách tiến trình giúp giao diện Streamlit không bị treo và tránh xung đột
event-loop của Playwright trên Windows. Mỗi job có:
  jobs/<name>.json  : trạng thái (pid, status, started, finished, message)
  jobs/<name>.log   : log stdout/stderr
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil

from .config import ROOT_DIR
from .storage import sub_dir


def _paths(project_id: str, name: str) -> tuple[Path, Path]:
    d = sub_dir(project_id, "jobs")
    return d / f"{name}.json", d / f"{name}.log"


def write_status(project_id: str, name: str, **fields) -> None:
    f, _ = _paths(project_id, name)
    data = read_status(project_id, name) or {}
    data.update(fields)
    _write_json(f, data)


def _write_json(f: Path, data: dict) -> None:
    """Ghi nguyên tử (file tạm rồi đổi tên): giao diện đọc giữa lúc ghi không gặp file dở dang."""
    tmp = f.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for _ in range(5):
        try:
            tmp.replace(f)
            return
        except PermissionError:      # Windows: file đích đang được tiến trình khác mở đọc -> thử lại
            time.sleep(0.05)
    tmp.replace(f)


def read_status(project_id: str, name: str) -> Optional[dict]:
    f, _ = _paths(project_id, name)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def start_job(project_id: str, name: str, module: str, args: list[str], env: Optional[dict] = None) -> dict:
    """Khởi chạy `python -m <module> <args>` ở chế độ nền."""
    if is_running(project_id, name):
        raise RuntimeError(f"Tác vụ '{name}' đang chạy")
    f, log = _paths(project_id, name)
    full_env = os.environ.copy()
    full_env["PYTHONIOENCODING"] = "utf-8"
    full_env["PYTHONUTF8"] = "1"
    if env:
        full_env.update(env)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log, "w", encoding="utf-8") as logf:      # tiến trình con giữ bản sao handle riêng
        proc = subprocess.Popen([sys.executable, "-u", "-m", module, *args], cwd=str(ROOT_DIR), env=full_env,
                                stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
    status = {"pid": proc.pid, "create_time": _create_time(proc.pid), "status": "running",
              "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "finished": "", "message": "", "module": module, "args": args}
    _write_json(f, status)
    return status


def _create_time(pid) -> Optional[float]:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, TypeError, ValueError):
        return None


def _own_process(st: dict) -> Optional[psutil.Process]:
    """Tiến trình của job nếu còn sống VÀ đúng là tiến trình đã khởi chạy (so thời điểm tạo, tránh PID bị tái sử dụng
    sau khi tắt/mở lại máy – nếu không sẽ báo "đang chạy" sai hoặc nút Dừng diệt nhầm tiến trình khác)."""
    try:
        p = psutil.Process(st.get("pid"))
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return None
        saved = st.get("create_time")
        if saved is not None:
            if abs(p.create_time() - float(saved)) > 1:
                return None
        else:   # trạng thái cũ chưa lưu create_time: kiểm tra dòng lệnh là tiến trình python chạy module của job
            mod = st.get("module") or ""
            if mod and mod not in " ".join(p.cmdline()):
                return None
        return p
    except (psutil.Error, TypeError, ValueError):
        return None


def is_running(project_id: str, name: str) -> bool:
    st = read_status(project_id, name)
    if not st or st.get("status") != "running":
        return False
    alive = _own_process(st) is not None
    if not alive:
        # tiến trình đã kết thúc mà không cập nhật trạng thái => coi là lỗi
        write_status(project_id, name, status="failed", finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     message=st.get("message") or "Tiến trình kết thúc bất thường (xem log)")
    return alive


def stop_job(project_id: str, name: str) -> None:
    st = read_status(project_id, name)
    if not st:
        return
    if _own_process(st) is not None:
        kill_tree(st.get("pid"))
    # tiến trình bị dừng đột ngột không kịp dọn file tạm chứa tài khoản/mật khẩu -> dọn tại đây
    from .accounts import cleanup_secret_files
    cleanup_secret_files(sub_dir(project_id, "runs"))
    write_status(project_id, name, status="stopped", finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 message="Người dùng đã dừng")


def kill_tree(pid: Optional[int]) -> None:
    if not pid:
        return
    try:
        parent = psutil.Process(pid)
        for c in parent.children(recursive=True):
            try:
                c.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def read_log(project_id: str, name: str, tail: int = 200) -> str:
    _, log = _paths(project_id, name)
    if not log.exists():
        return ""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(lines[-tail:])
