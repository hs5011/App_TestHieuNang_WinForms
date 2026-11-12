"""Lưu/đọc dự án trong thư mục workspace/projects/<id>/."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from unidecode import unidecode

from .config import PROJECTS_DIR
from .models import Project, _now

KEYRING_SERVICE = "PerfTool"
BROKEN_PROJECTS: dict[str, str] = {}     # id dự án -> lỗi khi nạp (lần gọi list_projects gần nhất)


def slugify(text: str) -> str:
    s = unidecode(text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "project"


def project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def sub_dir(project_id: str, name: str) -> Path:
    """Các thư mục con chuẩn: input, crawl, scripts, runs, charts, reports."""
    d = project_dir(project_id) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_projects() -> list[Project]:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    BROKEN_PROJECTS.clear()
    for f in sorted(PROJECTS_DIR.glob("*/project.json")):
        try:
            out.append(load_project(f.parent.name))
        except Exception as e:  # noqa: BLE001 - dự án hỏng: không làm hỏng danh sách, nhưng báo để người dùng biết
            BROKEN_PROJECTS[f.parent.name] = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            print(f"[PerfTool] Không nạp được dự án {f.parent.name}: {e}", file=sys.stderr)
    return sorted(out, key=lambda p: p.updated_at, reverse=True)


def create_project(name: str) -> Project:
    pid = f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(name)[:30]}"
    p = Project(id=pid)
    p.info.name = name
    save_project(p)
    return p


# cookie / header Authorization của phiên đăng nhập: chỉ lưu ở crawl/auth.json (1 nơi, cùng storage_state.json),
# KHÔNG lưu vào project.json; nạp dự án thì đọc bổ sung vào bộ nhớ (chế độ "cookie/token tĩnh" vẫn dùng được)
SESSION_FIELDS = {"cookies", "static_headers"}


def auth_file(project_id: str) -> Path:
    return PROJECTS_DIR / project_id / "crawl" / "auth.json"


def private_file(path: Path) -> None:
    """Chỉ chủ sở hữu được đọc/ghi (POSIX). Windows: thư mục người dùng đã giới hạn quyền theo tài khoản."""
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError:
        pass


def hydrate_session(p: Project, legacy: dict | None = None) -> None:
    """Nạp cookie/Authorization từ crawl/auth.json vào p.auth (trong bộ nhớ). Dự án cũ còn giữ các trường này trong
    project.json (legacy) -> chuyển sang auth.json để lần lưu sau project.json không còn chứa chúng."""
    f = auth_file(p.id)
    data: dict = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    changed = False
    for k in SESSION_FIELDS:
        if not data.get(k) and legacy and legacy.get(k):
            data[k] = legacy[k]
            changed = True
    if changed:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        private_file(f)
    if data.get("cookies"):
        p.auth.cookies = list(data["cookies"])
    if data.get("static_headers"):
        p.auth.static_headers = dict(data["static_headers"])


def load_project(project_id: str) -> Project:
    f = PROJECTS_DIR / project_id / "project.json"
    with open(f, encoding="utf-8") as fh:
        raw = json.load(fh)
    p = Project.model_validate(raw)
    hydrate_session(p, (raw.get("auth") or {}) if isinstance(raw, dict) else None)
    return p


def save_project(p: Project) -> None:
    p.updated_at = _now()
    f = project_dir(p.id) / "project.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(p.model_dump_json(indent=2, exclude={"auth": SESSION_FIELDS}), encoding="utf-8")
    tmp.replace(f)


def secret_keys(p: Project) -> list[str]:
    """Mọi khoá mật khẩu dự án có thể đã lưu trong Windows Credential Manager (tài khoản chính, tài khoản bổ sung,
    SSH/CSDL/Zabbix/Prometheus của phần thu số liệu máy chủ)."""
    from .monitor import sources
    m = p.monitor
    keys = [p.login.username, *p.login.extra_accounts]
    keys += [sources.ssh_secret_key(s) for s in m.servers if s.host]
    if m.db.host:
        keys.append(sources.db_secret_key(m.db))
    keys += [sources.ZABBIX_SECRET, sources.PROM_SECRET]
    return [k for k in dict.fromkeys(keys) if k]


def delete_project(project_id: str) -> None:
    """Xoá thư mục dự án (gồm phiên đăng nhập/cookie đã lưu) và mật khẩu đã lưu trong Windows Credential Manager."""
    try:
        for k in secret_keys(load_project(project_id)):
            delete_password(project_id, k)
    except Exception:  # noqa: BLE001 - project.json hỏng: vẫn xoá thư mục
        pass
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)


# ------------------------------------------------ mật khẩu (Windows Credential Manager)
def save_password(project_id: str, username: str, password: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, f"{project_id}:{username}", password)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_password(project_id: str, username: str) -> str:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, f"{project_id}:{username}") or ""
    except Exception:  # noqa: BLE001
        return ""


def delete_password(project_id: str, username: str) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, f"{project_id}:{username}")
    except Exception:  # noqa: BLE001 - không có mục nào để xoá
        pass
