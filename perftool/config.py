"""Nạp cấu hình và định nghĩa đường dẫn dùng chung."""
from __future__ import annotations

import copy
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "settings.yaml"
WORKSPACE_DIR = ROOT_DIR / "workspace"
PROJECTS_DIR = WORKSPACE_DIR / "projects"
TEMPLATES_DIR = Path(__file__).resolve().parent / "scriptgen" / "templates"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings() -> dict[str, Any]:
    """Trả về bản sao cấu hình (tránh sửa nhầm cache)."""
    return copy.deepcopy(_load_raw())


def get(path: str, default: Any = None) -> Any:
    """Lấy giá trị theo đường dẫn dạng 'crawler.timeout_ms'."""
    node: Any = _load_raw()
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return copy.deepcopy(node)


def insecure_tls(login: Any = None) -> bool:
    """Có bỏ qua kiểm tra chứng chỉ HTTPS không: tuỳ chọn của dự án (bước 1) hoặc security.skip_tls_verify chung."""
    return bool(getattr(login, "skip_tls_verify", False) or get("security.skip_tls_verify", False))


def reload() -> None:
    _load_raw.cache_clear()


def find_executable(configured: str | None, names: list[str], extra_dirs: list[str] | None = None) -> str | None:
    """Tìm file thực thi: ưu tiên đường dẫn cấu hình, sau đó PATH, sau đó thư mục gợi ý."""
    if configured and Path(configured).exists():
        return str(Path(configured))
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for d in extra_dirs or []:
        for n in names:
            cand = Path(d) / n
            if cand.exists():
                return str(cand)
    return None


def k6_executable() -> str | None:
    return find_executable(get("tools.k6_path"), ["k6", "k6.exe"], [r"C:\Program Files\k6"])


def jmeter_executable() -> str | None:
    extra = [str(p / "bin") for p in Path("C:/Tools").glob("apache-jmeter*")] if Path("C:/Tools").exists() else []
    return find_executable(get("tools.jmeter_path"), ["jmeter.bat", "jmeter"], extra)
