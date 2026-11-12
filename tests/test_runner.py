r"""Kiểm thử dọn kết quả cũ khi chạy lại một lượt (không cần k6/JMeter).  Chạy: .venv\Scripts\python -m pytest -q"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

from perftool.models import RunInfo
from perftool.runner.executor import clear_previous_outputs, has_fresh_data


def _run(tmp_path, tool: str) -> RunInfo:
    d = tmp_path / "runs" / f"r_{tool}"
    d.mkdir(parents=True)
    if tool == "k6":
        (d / "script.js").write_text("//")
        raw, summary = d / "raw.csv", d / "summary.json"
    else:
        (d / "plan.jmx").write_text("<x/>")
        raw, summary = d / "results.jtl", d / "html-report"
    return RunInfo(run_id=d.name, tool=tool, script_file=str(next(d.iterdir())), raw_file=str(raw),
                   summary_file=str(summary), log_file=str(d / "tool.log"))


def test_clear_previous_outputs_jmeter_removes_stale_report(tmp_path):
    run = _run(tmp_path, "jmeter")
    d = tmp_path / "runs" / "r_jmeter"
    (d / "results.jtl").write_text("timeStamp,elapsed\n1,2\n")
    (d / "html-report" / "content").mkdir(parents=True)
    (d / "html-report" / "index.html").write_text("cu")
    removed = clear_previous_outputs(run)
    assert {p.name for p in removed} == {"results.jtl", "html-report"}
    assert not (d / "results.jtl").exists() and not (d / "html-report").exists()
    assert (d / "plan.jmx").exists()  # script giữ nguyên


def test_clear_previous_outputs_k6_and_no_op_when_clean(tmp_path):
    run = _run(tmp_path, "k6")
    d = tmp_path / "runs" / "r_k6"
    (d / "raw.csv").write_text("metric_name\n")
    (d / "summary.json").write_text("{}")
    assert len(clear_previous_outputs(run)) == 2
    assert not (d / "raw.csv").exists() and not (d / "summary.json").exists()
    assert clear_previous_outputs(run) == []  # chạy lần 2: không còn gì để xoá


def test_clear_previous_outputs_ignores_paths_outside_run_dir(tmp_path):
    run = _run(tmp_path, "jmeter")
    outside = tmp_path / "khac.jtl"
    outside.write_text("x")
    run.raw_file = str(outside)
    clear_previous_outputs(run)
    assert outside.exists()


def test_has_fresh_data_rejects_stale_raw_file(tmp_path):
    run = _run(tmp_path, "jmeter")
    raw = tmp_path / "runs" / "r_jmeter" / "results.jtl"
    raw.write_text("timeStamp,elapsed\n1,2\n")
    old = time.time() - 3600
    os.utime(raw, (old, old))
    run.started_at = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert not has_fresh_data(run)  # file của lần chạy trước -> không tính
    raw.write_text("timeStamp,elapsed\n3,4\n")
    assert has_fresh_data(run)
    raw.write_text("")
    assert not has_fresh_data(run)
