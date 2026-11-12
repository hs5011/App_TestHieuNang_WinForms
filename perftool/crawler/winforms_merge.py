"""Gộp dữ liệu ghi WinForms vào dự án + đổi tên màn hình đã ghi."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import AuthCapture, ModuleInfo
from ..storage import hydrate_session, sub_dir
from .manual import load_record
from .merge import apply_record, load_sessions
from .winforms import WINFORMS_MODULE, screen_url


def merge_winforms(p) -> int:
    """Gộp crawl/record.json (+ xác thực nhận diện được) vào dự án WinForms. Trả về số đoạn màn hình đã gộp."""
    for s in load_sessions(p):
        mod = (s.get("module") or WINFORMS_MODULE).strip() or WINFORMS_MODULE
        if not any(m.name == mod for m in p.modules):
            p.modules.append(ModuleInfo(name=mod, url=screen_url(mod, ""), enabled=True))
    n = apply_record(p)
    f = sub_dir(p.id, "crawl") / "winforms_auth.json"
    if f.exists():
        try:
            a = AuthCapture.model_validate_json(f.read_text(encoding="utf-8"))
            if a.login_url:
                p.auth = a
        except Exception:  # noqa: BLE001
            pass
    hydrate_session(p)           # Authorization/cookie riêng tư từ crawl/auth.json
    return n


def rename_segments(record_file: Path, edits: list[dict]) -> int:
    """Đổi tên màn hình đã ghi: edits = [{"session": started, "index": i, "title": tên mới}]. Trả về số màn hình đã đổi."""
    data = load_record(record_file)
    changed = 0
    for e in edits:
        title = str(e.get("title") or "").strip()
        if not title:
            continue
        for s in data["sessions"]:
            if s.get("started") != e.get("session"):
                continue
            segs = s.get("segments") or []
            i = int(e.get("index", -1))
            if 0 <= i < len(segs) and segs[i].get("title") != title:
                url = screen_url(s.get("module") or WINFORMS_MODULE, title)
                seg = segs[i]
                seg["title"], seg["url"], seg["page_url"] = title, url, url
                for r in seg.get("requests", []):
                    r["page_url"] = url
                changed += 1
    if changed:
        tmp = record_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(record_file)
    return changed
