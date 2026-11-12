"""CLI chạy lần lượt các lượt test đã lập kế hoạch (tiến trình nền).

  python -m perftool.runner.cli --project <id> --runs <run_id1,run_id2,...>

Tài khoản truyền qua biến môi trường PERFTOOL_ACCOUNTS (JSON [[user, pass], ...]) hoặc
PERFTOOL_USERNAME / PERFTOOL_PASSWORD (1 tài khoản).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

from .. import jobs
from ..accounts import cleanup_secret_files
from ..monitor import runtime as monitor
from ..storage import load_project, sub_dir
from ..scriptgen.profile import scenario_name
from .executor import _now, execute, has_fresh_data, load_run_file, save_run_file

JOB = "run_tests"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--runs", required=True)
    a = ap.parse_args()
    accounts = _accounts_from_env()
    runs_dir = sub_dir(a.project, "runs")
    cleanup_secret_files(runs_dir)          # dọn file tạm còn sót từ lần chạy bị ngắt trước đó
    ids = [r for r in a.runs.split(",") if r]
    failed = 0
    project = None
    mon_proc = None
    try:
        project = load_project(a.project)
        mon_proc = monitor.start_live(project, log)
    except Exception as e:  # noqa: BLE001  – lỗi thu số liệu máy chủ không được làm hỏng lượt test
        log(f"Không khởi động được thu số liệu máy chủ: {e}")
    try:
        for i, rid in enumerate(ids, 1):
            run = load_run_file(runs_dir / rid)
            if not run:
                log(f"Không tìm thấy lượt chạy {rid}")
                continue
            log(f"===== Lượt {i}/{len(ids)}: {run.tool.upper()} | UC {run.uc_code} | {scenario_name(run.scenario_type)} =====")
            jobs.write_status(a.project, JOB, current=rid, progress=f"{i}/{len(ids)}")
            run.status, run.started_at = "running", _now()
            save_run_file(run)
            try:
                rc = execute(run, accounts, log,
                             on_start=lambda pid: jobs.write_status(a.project, JOB, child_pid=pid))
            except Exception as e:  # noqa: BLE001
                log(f"LỖI: {e}")
                rc = -1
            run.returncode = rc
            run.finished_at = _now()
            # chỉ tính dữ liệu được ghi trong lượt này, tránh báo nhầm kết quả cũ là mới
            has_data = has_fresh_data(run)
            # k6 trả mã 99 khi vượt threshold nhưng vẫn có dữ liệu hợp lệ; mã khác 0 còn lại = công cụ lỗi/bị dừng
            # -> "failed" dù có vài dòng dữ liệu (tránh đưa kết quả dở dang vào bước 8–10 như một lượt hoàn chỉnh)
            ok_rc = rc == 0 or (run.tool == "k6" and rc == 99)
            run.status = "done" if ok_rc and has_data else "failed"
            if run.status == "failed":
                failed += 1
                if has_data:
                    log(f"Lượt {rid} có dữ liệu nhưng công cụ thoát lỗi (mã {rc}) – không đưa vào phân tích; "
                        "xem tool.log của lượt chạy.")
                elif ok_rc:
                    log(f"Lượt {rid}: công cụ thoát bình thường nhưng không ghi được dòng kết quả nào.")
            save_run_file(run)
            log(f"Kết thúc lượt {rid}: mã thoát {rc}, trạng thái {run.status}")
            if project and project.monitor.enabled:
                try:
                    monitor.fetch_history(project, run.started_at, run.finished_at, rid, monitor.env_secrets(), log)
                except Exception as e:  # noqa: BLE001
                    log(f"Lấy số liệu Zabbix/Prometheus lỗi: {e}")
        jobs.write_status(a.project, JOB, status="done", finished=_now(),
                          message=f"Đã chạy xong {len(ids)} lượt ({failed} lỗi)")
        return 0
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        jobs.write_status(a.project, JOB, status="failed", finished=_now(), message=str(e)[:300])
        return 1
    finally:
        monitor.stop_live(mon_proc, log)
        cleanup_secret_files(runs_dir)


def _accounts_from_env() -> list[tuple[str, str]]:
    """PERFTOOL_ACCOUNTS = JSON [[user, pass], ...]; tương thích ngược PERFTOOL_USERNAME / PERFTOOL_PASSWORD."""
    raw = os.environ.get("PERFTOOL_ACCOUNTS", "")
    if raw:
        try:
            acc = [(str(u), str(p)) for u, p in json.loads(raw) if str(u).strip()]
            if acc:
                return acc
        except (ValueError, TypeError):
            pass
    user, pwd = os.environ.get("PERFTOOL_USERNAME", ""), os.environ.get("PERFTOOL_PASSWORD", "")
    return [(user, pwd)] if user or pwd else []


if __name__ == "__main__":
    sys.exit(main())
