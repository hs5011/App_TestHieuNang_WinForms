"""Gắn thu thập số liệu máy chủ vào tiến trình chạy test (runner) và lấy lịch sử Zabbix/Prometheus."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Callable, Optional

from ..config import ROOT_DIR
from ..jobs import kill_tree
from ..models import Project
from . import sources, store
from .collector import has_live, secrets_from_env

Log = Callable[[str], None]


def start_live(p: Project, log: Log) -> Optional[subprocess.Popen]:
    if not has_live(p.monitor):
        return None
    logf = open(store.monitor_dir(p.id) / "collector.log", "a", encoding="utf-8")
    stop_file = store.monitor_dir(p.id) / "collector.stop"
    stop_file.unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-u", "-m", "perftool.monitor.collector", "--project", p.id,
                             "--parent", str(os.getpid()), "--stop-file", str(stop_file)],
                            cwd=str(ROOT_DIR), env=_collector_env(), stdout=logf, stderr=subprocess.STDOUT)
    logf.close()      # tiến trình con đã giữ bản sao handle
    proc.perftool_stop_file = stop_file
    log(f"Bắt đầu thu số liệu tài nguyên máy chủ (tiến trình {proc.pid}, nhật ký monitor/collector.log)")
    return proc


def _collector_env() -> dict[str, str]:
    """Collector chỉ cần mật khẩu máy chủ (PERFTOOL_MONITOR_SECRETS), không cần mật khẩu tài khoản kiểm thử."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PERFTOOL_ACCOUNTS", "PERFTOOL_PASSWORD")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def stop_live(proc: Optional[subprocess.Popen], log: Log) -> None:
    if not proc:
        return
    # dừng êm qua file cờ để collector kịp đóng nguồn đọc; quá hạn mới diệt cả cây tiến trình
    stop_file = getattr(proc, "perftool_stop_file", None)
    if stop_file:
        stop_file.touch()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    if stop_file:
        stop_file.unlink(missing_ok=True)
    log("Đã dừng thu số liệu tài nguyên máy chủ.")


def fetch_history(p: Project, started_at: str, finished_at: str, tag: str, secrets: dict[str, str],
                  log: Log) -> int:
    """Lấy số liệu Zabbix / Prometheus trong khung giờ 1 lượt chạy (các hệ thống này tự lưu lịch sử)."""
    cfg = p.monitor
    if not cfg.enabled or not started_at or not finished_at:
        return 0
    t0 = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=30)
    t1 = datetime.strptime(finished_at, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=30)
    total = 0
    if "zabbix" in cfg.tools and cfg.zabbix_url:
        hosts = [s for s in cfg.servers if s.zabbix_host]
        try:
            z = sources.Zabbix(cfg.zabbix_url, cfg.zabbix_user, secrets.get(sources.ZABBIX_SECRET, ""))
            rows_by = []
            for s in hosts:
                rows = z.history(s.zabbix_host, sources.zabbix_items(cfg), t0, t1)
                rows_by.append((s.name or s.host or s.zabbix_host, rows))
            path = store.monitor_dir(p.id) / f"zabbix_{tag}.csv"
            path.unlink(missing_ok=True)
            sink = store.CsvSink(path)
            for name, rows in rows_by:
                total += sink.write(name, rows, "zabbix")
            log(f"Zabbix: lấy {sum(len(r) for _, r in rows_by)} giá trị cho {len(hosts)} máy chủ")
        except Exception as e:  # noqa: BLE001
            log(f"Zabbix: LỖI {e}")
    if "prometheus" in cfg.tools and cfg.prom_url:
        hosts = [s for s in cfg.servers if s.prom_instance]
        try:
            token = secrets.get(sources.PROM_SECRET, "")
            path = store.monitor_dir(p.id) / f"prometheus_{tag}.csv"
            path.unlink(missing_ok=True)
            sink = store.CsvSink(path)
            n = 0
            for s in hosts:
                rows = sources.prom_history(cfg.prom_url, token, sources.prom_queries(cfg), s.prom_instance, t0, t1,
                                            step_s=max(cfg.interval_s, 5))
                n += sink.write(s.name or s.host or s.prom_instance, rows, "prometheus")
            total += n
            log(f"Prometheus: lấy {n} giá trị cho {len(hosts)} máy chủ")
        except Exception as e:  # noqa: BLE001
            log(f"Prometheus: LỖI {e}")
    return total


def env_secrets() -> dict[str, str]:
    return secrets_from_env()
