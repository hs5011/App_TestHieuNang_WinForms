"""Addon mitmproxy: ghi từng request/response của ứng dụng WinForms ra JSONL, tuỳ chọn CHẶN request ghi dữ liệu.

Chạy bởi winforms_cli.py (mitmdump -s mitm_addon.py). Cấu hình qua biến môi trường:
  PERFTOOL_WF_OUT      file JSONL đầu ra
  PERFTOOL_WF_HOSTS    danh sách máy chủ API (phân tách bằng dấu phẩy, trống = ghi mọi máy chủ)
  PERFTOOL_WF_GUARD    off | keyword | strict  (mức chặn ghi, dùng chung quy tắc crawler.guard)
  PERFTOOL_WF_USER     tên đăng nhập (để tạo mẫu body đăng nhập; mật khẩu lấy từ PERFTOOL_PASSWORD)
Mật khẩu/OTP trong body được che trước khi ghi; Authorization chỉ ghi chữ ký băm (giá trị thật chỉ ở bộ nhớ,
ghi riêng vào file riêng tư khi kết thúc – xem winforms_cli).
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlparse

from perftool.crawler import winforms as wf
from perftool.crawler.guard import is_write_request
from perftool.crawler.login_capture import has_secret_field, login_template, redact_body

OUT = os.environ.get("PERFTOOL_WF_OUT", "winforms_flows.jsonl")
HOSTS = [h.strip().lower() for h in os.environ.get("PERFTOOL_WF_HOSTS", "").split(",") if h.strip()]
GUARD = os.environ.get("PERFTOOL_WF_GUARD", "keyword")
USER = os.environ.get("PERFTOOL_WF_USER", "")
PWD = os.environ.get("PERFTOOL_PASSWORD", "")
AUTH_FILE = os.environ.get("PERFTOOL_WF_AUTH", "")


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.blocked = 0
        self.count = 0
        self.last_auth = ""

    def _wanted(self, flow) -> bool:
        if not HOSTS:
            return True
        host = (flow.request.pretty_host or "").lower()
        return any(host == h or host.endswith("." + h) or h in host for h in HOSTS)

    # chặn ghi ngay khi nhận request (trước khi tới máy chủ)
    def request(self, flow) -> None:
        if GUARD == "off" or not self._wanted(flow):
            return
        from mitmproxy import http
        r = flow.request
        if wf.is_api_flow({"url": r.pretty_url, "ctype": r.headers.get("content-type", "")}) and \
                is_write_request(r.method, r.pretty_url, "xhr", GUARD):
            flow.metadata["perftool_blocked"] = True
            self.blocked += 1
            flow.response = http.Response.make(
                403, json.dumps({"error": "PerfTool: request ghi dữ liệu đã bị chặn khi ghi nhận thao tác"}),
                {"Content-Type": "application/json", "X-PerfTool-Blocked": "1"})

    def response(self, flow) -> None:
        if not self._wanted(flow):
            return
        r, resp = flow.request, flow.response
        if resp is None:
            return
        try:
            ctype = resp.headers.get("content-type", "")
            f = {"t": r.timestamp_start, "method": r.method.upper(), "url": r.pretty_url, "status": resp.status_code,
                 "duration_ms": round(((resp.timestamp_end or resp.timestamp_start) - r.timestamp_start) * 1000, 1),
                 "ctype": ctype, "req_ctype": r.headers.get("content-type", ""), "resp_size": len(resp.content or b""),
                 "headers": {k: v for k, v in r.headers.items() if wf.is_custom_header(k)},
                 "blocked": bool(flow.metadata.get("perftool_blocked"))}
            auth = r.headers.get("authorization", "")
            if auth:
                f["auth_sig"] = wf._sig(auth.split(" ", 1)[-1])
                if auth != self.last_auth:
                    self.last_auth = auth
                    self._save_auth()          # ghi ngay: proxy bị dừng đột ngột (terminate) sẽ không chạy done()
            if not f["blocked"] and "json" in ctype.lower() and resp.content:
                try:
                    j = json.loads(resp.get_text(strict=False))
                    f["rows"] = wf.json_rows(j)
                    tp = wf.find_token_path(j)
                    if tp:
                        f["token_path"] = tp
                except ValueError:
                    pass
            body = r.get_text(strict=False) if r.content else None
            if body:
                req_ct = f["req_ctype"]
                if r.method.upper() in ("POST", "PUT") and (has_secret_field(body, req_ct) or (PWD and PWD in body)):
                    tpl, notes = login_template(body, req_ct, USER, PWD)
                    f["login"] = {"template": tpl, "ctype": req_ct, "notes": notes, "token_path": f.get("token_path", "")}
                red, hit = redact_body(body, [PWD] if PWD else [])
                f["post_data"] = (red or "")[:4000]
                f["sensitive"] = bool(hit)
            with self.lock:
                with open(OUT, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(f, ensure_ascii=False) + "\n")
                self.count += 1
        except Exception as e:  # noqa: BLE001 - không để lỗi ghi làm gián đoạn ứng dụng đang được thao tác
            print(f"[perftool] lỗi ghi flow: {e}", flush=True)

    def _save_auth(self) -> None:
        if AUTH_FILE and self.last_auth:
            try:
                with open(AUTH_FILE, "w", encoding="utf-8") as fh:
                    json.dump({"Authorization": self.last_auth}, fh)
            except OSError:
                pass

    def done(self) -> None:
        self._save_auth()
        print(f"[perftool] đã ghi {self.count} request, chặn {self.blocked} request ghi.", flush=True)


addons = [Recorder()]
