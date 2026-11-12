"""Tiến trình nền thu số liệu tài nguyên máy chủ trong lúc chạy test (bước 7).

  python -m perftool.monitor.collector --project <id> --parent <pid>

Được runner tự khởi động khi bật thu thập ở bước 6; tự dừng khi tiến trình chạy test (parent) kết thúc.
Mật khẩu SSH/CSDL truyền qua biến môi trường PERFTOOL_MONITOR_SECRETS (JSON {khoá: mật khẩu}), không ghi ra file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime

import psutil

from ..storage import load_project
from . import sources, store


_LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    with _LOG_LOCK:          # nhiều luồng cùng ghi -> tránh dính dòng
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def secrets_from_env() -> dict[str, str]:
    try:
        return json.loads(os.environ.get("PERFTOOL_MONITOR_SECRETS", "") or "{}")
    except ValueError:
        return {}


def live_targets(cfg) -> list:
    """Máy chủ thu trực tiếp bằng Performance Monitor (SSH hoặc máy chạy test)."""
    if "perfmon" not in cfg.tools:
        return []
    # máy chạy test (local) không cần địa chỉ IP; SSH bắt buộc có host
    return [s for s in cfg.servers if s.access == "local" or (s.host and s.access == "ssh")]


def server_name(s) -> str:
    return s.name or s.host or "Máy chạy test"


def has_live(cfg) -> bool:
    return cfg.enabled and (bool(live_targets(cfg)) or (cfg.db.enabled and bool(cfg.db.host)))


def _perfmon_worker(s, pwd: str, interval: int, sink: store.CsvSink, stop: threading.Event) -> None:
    name = server_name(s)
    while not stop.is_set():
        try:
            if s.access != "local" and not s.ram_total_mb:     # local đọc % RAM trực tiếp bằng psutil
                try:
                    s.ram_total_mb = sources.total_ram_mb(s, pwd)
                except Exception as e:  # noqa: BLE001
                    log(f"[{name}] Không lấy được tổng RAM ({e}) – dùng % Committed Bytes In Use")
            log(f"[{name}] Bắt đầu đọc Performance Monitor ({'máy này' if s.access == 'local' else 'SSH'})")
            n = 0
            for ts, metric, value in sources.perfmon_stream(s, pwd, interval, stop.is_set):
                n += sink.write(name, [(ts, metric, value)], "perfmon")
            log(f"[{name}] Kết thúc đọc bộ đếm hiệu năng ({n} giá trị)")
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] LỖI: {e}")
        if stop.wait(10):         # mất kết nối -> thử lại sau 10 giây
            break


def _db_worker(db, pwd: str, interval: int, sink: store.CsvSink, stop: threading.Event) -> None:
    name = f"CSDL {db.host}"
    sql = sources.db_query(db)
    try:
        sources.check_readonly(sql)
    except ValueError as e:
        log(f"[{name}] {e}")
        return
    while not stop.is_set():
        conn = None
        try:
            conn = sources.db_connect(db, pwd)
            log(f"[{name}] Đã kết nối CSDL, truy vấn số kết nối mỗi {interval} giây")
            while not stop.is_set():
                v = sources.db_sample(conn, sql)
                sink.write(name, [(datetime.now().replace(microsecond=0), "db_conn", v)], "db")
                if stop.wait(interval):
                    break
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] LỖI: {e}")
        finally:
            try:
                conn and conn.close()
            except Exception:  # noqa: BLE001
                pass
        if stop.wait(10):
            break


def _create_time(pid: int):
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--parent", type=int, default=0)
    ap.add_argument("--stop-file", default="", help="file cờ: runner tạo file này để yêu cầu dừng êm")
    a = ap.parse_args()
    p = load_project(a.project)
    cfg = p.monitor
    secrets = secrets_from_env()
    interval = max(int(cfg.interval_s or 5), 1)
    sink = store.CsvSink(store.monitor_dir(p.id) / f"live_{datetime.now():%Y%m%d-%H%M%S}.csv")
    stop = threading.Event()
    threads = []
    for s in live_targets(cfg):
        pwd = secrets.get(sources.ssh_secret_key(s), "")
        threads.append(threading.Thread(target=_perfmon_worker, args=(s, pwd, interval, sink, stop), daemon=True))
    if cfg.db.enabled and cfg.db.host:
        pwd = secrets.get(sources.db_secret_key(cfg.db), "")
        threads.append(threading.Thread(target=_db_worker, args=(cfg.db, pwd, interval, sink, stop), daemon=True))
    if not threads:
        log("Không có nguồn thu trực tiếp nào được cấu hình.")
        return 0
    for t in threads:
        t.start()
    log(f"Đang thu số liệu vào {sink.path.name} (mỗi {interval} giây)")
    parent_ct = _create_time(a.parent) if a.parent else None
    try:
        while not stop.is_set():
            # so cả thời điểm tạo tiến trình: PID của runner đã kết thúc có thể bị tiến trình khác dùng lại
            if a.parent and _create_time(a.parent) != parent_ct:
                break
            if a.stop_file and os.path.exists(a.stop_file):
                break
            time.sleep(1)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        log("Đã dừng thu số liệu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
