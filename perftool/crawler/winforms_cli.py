"""CLI chạy ở tiến trình nền cho dự án WinForms.

  python -m perftool.crawler.winforms_cli --project <id> --mode record [--port 8899] [--hosts a.com,b.com]
                                          [--guard keyword|strict|off] [--launch "C:\\App\\App.exe"]
  python -m perftool.crawler.winforms_cli --project <id> --mode har --har file.har [--gap 8] [--hosts ...]

record: mở proxy ghi (mitmdump) tại cổng --port; người dùng trỏ ứng dụng WinForms vào proxy và thao tác, bấm "Đánh dấu
        màn hình" trên PerfTool mỗi khi sang màn hình mới. Kết thúc khi có file cờ crawl/record.stop (nút Dừng) hoặc proxy dừng.
har:    nhập file HAR (Fiddler / Charles / mitmproxy / DevTools) – chia màn hình theo mốc (nếu có) hoặc khoảng nghỉ.
Kết quả: crawl/record.json (nối thêm), crawl/auth.json (riêng tư). Mật khẩu qua PERFTOOL_PASSWORD.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback

from .. import jobs
from ..config import ROOT_DIR
from ..storage import load_project, private_file, sub_dir
from . import winforms as wf

JOB = "crawl_record"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Dừng proxy và mọi tiến trình con (python.exe của venv trên Windows là bộ khởi chạy – dừng riêng nó sẽ để lại
    mitmdump chạy ngầm giữ cổng)."""
    import psutil
    try:
        root = psutil.Process(proc.pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=8)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def finish(project_id: str, work, flows: list[dict], gap: float, base_url: str, note: str = "") -> int:
    """flows + mốc -> record.json, auth.json. Trả về số màn hình."""
    markers = wf.read_markers(work / "winforms_markers.jsonl")
    groups = wf.split_screens(flows, markers, gap)
    sessions = wf.build_sessions(groups)
    if sessions:
        wf.append_record(work / "record.json", sessions)
    auth = wf.extract_auth(flows, base_url)
    if auth.login_url or auth.static_headers:
        from ..storage import SESSION_FIELDS
        f = work / "auth.json"
        old = {}
        if f.exists():
            try:
                old = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                old = {}
        data = {**old, **auth.model_dump()}
        if not auth.static_headers and old.get("static_headers"):
            data["static_headers"] = old["static_headers"]
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        private_file(f)
        (work / "winforms_auth.json").write_text(
            json.dumps(auth.model_dump(exclude=SESSION_FIELDS), ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Đã nhận diện xác thực: {auth.login_method + ' ' + auth.login_url if auth.login_url else 'header Authorization'}"
            + (f" (token: {auth.token_json_path})" if auth.token_json_path else ""))
    for n in wf.find_login_notes(flows):
        log(f"Lưu ý đăng nhập: {n}.")
    n_req = sum(len(g["flows"]) for g in groups)
    log(f"Hoàn tất{note}: {len(groups)} màn hình, {n_req} request"
        f"{'' if markers else ' (không có mốc – tách theo khoảng nghỉ ' + str(gap) + ' giây)'}.")
    return len(groups)


def run_record(project_id: str, a) -> int:
    project = load_project(project_id)
    work = sub_dir(project_id, "crawl")
    out = work / "winforms_flows.jsonl"
    auth_tmp = work / "winforms_auth_header.json"
    stop = work / "record.stop"
    out.unlink(missing_ok=True)
    auth_tmp.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)
    (work / "winforms_markers.jsonl").unlink(missing_ok=True)
    env = os.environ.copy()
    env.update({"PERFTOOL_WF_OUT": str(out), "PERFTOOL_WF_HOSTS": a.hosts, "PERFTOOL_WF_GUARD": a.guard,
                "PERFTOOL_WF_USER": project.login.username, "PERFTOOL_WF_AUTH": str(auth_tmp),
                "PYTHONPATH": str(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", ""),
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    cmd = [sys.executable, "-c", "from mitmproxy.tools.main import mitmdump; mitmdump()",
           "-s", str(ROOT_DIR / "perftool" / "crawler" / "mitm_addon.py"), "--listen-port", str(a.port),
           "--set", "termlog_verbosity=warn", "--set", "flow_detail=0"]
    if project.login.skip_tls_verify:
        cmd += ["--set", "ssl_insecure=true"]
    log(f"Mở proxy ghi tại 127.0.0.1:{a.port}. Hãy trỏ ứng dụng WinForms qua proxy này (xem hướng dẫn ở bước 3).")
    log("Chứng chỉ HTTPS: nếu ứng dụng dùng HTTPS, cài chứng chỉ mitmproxy (~/.mitmproxy/mitmproxy-ca-cert.cer) "
        "vào 'Trusted Root Certification Authorities' của Windows – chỉ trên máy kiểm thử.")
    if a.guard != "off":
        log(f"Bộ chặn ghi đang BẬT (mức {a.guard}): Lưu/Xoá/Duyệt… trả về lỗi 403, KHÔNG ghi dữ liệu thật.")
    proc = subprocess.Popen(cmd, cwd=str(ROOT_DIR), env=env)
    app = None
    if a.launch:
        penv = os.environ.copy()
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            penv[k] = f"http://127.0.0.1:{a.port}"
        try:
            app = subprocess.Popen([a.launch], env=penv, cwd=os.path.dirname(a.launch) or None)
            log(f"Đã mở ứng dụng: {a.launch} (biến HTTP_PROXY/HTTPS_PROXY trỏ về proxy; ứng dụng .NET Framework cũ có thể "
                "bỏ qua biến này – khi đó đặt proxy hệ thống hoặc <defaultProxy> trong App.config).")
        except OSError as e:
            log(f"Không mở được ứng dụng: {e}")
    reason = "proxy đã dừng"
    deadline = time.time() + 60 * 180
    try:
        while time.time() < deadline:
            if stop.exists():
                reason = "bấm Kết thúc ghi"
                break
            if proc.poll() is not None:
                break
            time.sleep(0.7)
    finally:
        _kill_tree(proc)
        if app is not None and app.poll() is None:
            log("Ứng dụng WinForms vẫn đang mở (PerfTool không tự đóng) – lưu lượng mới sẽ không còn được ghi.")
        time.sleep(0.3)
    flows = wf.read_jsonl(out)
    if auth_tmp.exists():
        try:
            hdr = json.loads(auth_tmp.read_text(encoding="utf-8")).get("Authorization", "")
            for f in flows:
                f["_authorization"] = hdr
        except ValueError:
            pass
        auth_tmp.unlink(missing_ok=True)
    log(f"Kết thúc ghi ({reason}): {len(flows)} request.")
    finish(project_id, work, flows, a.gap, project.login.base_url)
    out.unlink(missing_ok=True)       # JSONL thô không còn cần (đã gộp vào record.json; không giữ lại body)
    return 0


def run_har(project_id: str, a) -> int:
    project = load_project(project_id)
    work = sub_dir(project_id, "crawl")
    har = json.loads(open(a.har, encoding="utf-8-sig").read())
    flows = wf.flows_from_har(har, [os.environ.get("PERFTOOL_PASSWORD", "")], project.login.username,
                              os.environ.get("PERFTOOL_PASSWORD", ""))
    hosts = [h.strip().lower() for h in a.hosts.split(",") if h.strip()]
    if hosts:
        flows = [f for f in flows if any(h in f["url"].lower() for h in hosts)]
    log(f"Đọc HAR: {len(flows)} request" + (f" (lọc theo máy chủ {', '.join(hosts)})" if hosts else "") + ".")
    top = list(wf.api_hosts(flows).items())[:3]
    if top:
        log("Máy chủ nhận nhiều request nhất: " + ", ".join(f"{h} ({n})" for h, n in top))
    (work / "winforms_markers.jsonl").unlink(missing_ok=True)   # HAR: tách theo khoảng nghỉ; đổi tên màn hình ở bước 3
    finish(project_id, work, flows, a.gap, project.login.base_url, note=" nhập HAR")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", choices=["record", "har"], required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--hosts", default="", help="máy chủ API cần ghi (phân tách dấu phẩy); trống = tất cả")
    ap.add_argument("--guard", choices=["keyword", "strict", "off"], default="keyword")
    ap.add_argument("--launch", default="", help="đường dẫn .exe WinForms để mở kèm (đặt biến proxy)")
    ap.add_argument("--har", default="")
    ap.add_argument("--gap", type=float, default=wf.GAP_DEFAULT_S)
    a = ap.parse_args()
    try:
        rc = run_record(a.project, a) if a.mode == "record" else run_har(a.project, a)
        jobs.write_status(a.project, JOB, status="done", finished=wf.now(), message="Hoàn tất")
        return rc
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        jobs.write_status(a.project, JOB, status="failed", finished=wf.now(), message=str(e)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
