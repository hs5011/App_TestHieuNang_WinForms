"""Bộ test biên / âm / bảo mật (tạo bằng agent kiểm thử 2026-09-25). Chỉ dùng tmp_path, chặn mạng ngoài localhost."""
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Mọi thao tác lưu dự án đều vào tmp_path; chặn kết nối mạng ra ngoài localhost."""
    import perftool.storage as storage
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")

    real_connect = socket.socket.connect

    def guarded(self, addr, *a, **kw):
        host = addr[0] if isinstance(addr, tuple) else addr
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(f"Test không được kết nối mạng ngoài: {addr}")
        return real_connect(self, addr, *a, **kw)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield
