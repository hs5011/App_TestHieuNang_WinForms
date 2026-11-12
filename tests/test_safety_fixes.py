"""Kiểm thử hồi quy 4 lỗi mức Cao (2026-09-25): phân loại request ghi, bộ chặn ghi, thu số liệu máy chủ local,
lượt chạy bị dừng giữa chừng.  Chạy: .venv\\Scripts\\python -m pytest -q"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

from perftool.analysis.complexity import build_scenario
from perftool.crawler.guard import is_query_request, is_write_request
from perftool.models import (CapturedRequest, LoginConfig, ModuleInfo, MonitorConfig, MonitorServer, PageInfo,
                             Project, RunInfo, UCScore)
from perftool.monitor import collector, runtime, sources
from perftool.results.parsers import parse_jtl


# ---------------------------------------------------------------- 1. request ghi không được bật sẵn trong kịch bản

def test_write_posts_are_not_treated_as_queries():
    for u in ["https://x.vn/api/budget/save", "https://x.vn/api/targets/create", "https://x.vn/api/checklist/delete",
              "https://x.vn/api/widgets/update", "https://widget.example.vn/api/tasks/save",
              "https://x.vn/api/tasks?action=getlist", "https://x.vn/api/tasks"]:
        assert not is_query_request("POST", u), u
    for m in ("PUT", "PATCH", "DELETE"):
        assert not is_query_request(m, "https://x.vn/api/tasks/search")


def test_query_posts_stay_enabled():
    api = "https://ntqlcqs.example.vn/api/services/app"
    for u in [f"{api}/Task/GetAll", f"{api}/Task/GetForEdit", "https://x.vn/api/tasks/search",
              f"{api}/DRViewer/PostData", f"{api}/Report/ExecuteStoreWithParam", "https://x.vn/notify/negotiate"]:
        assert is_query_request("POST", u), u
    assert is_query_request("GET", "https://x.vn/api/tasks/list?id=1")
    assert not is_query_request("GET", "https://x.vn/api/tasks/delete?id=1")   # GET ghi dữ liệu -> tắt mặc định


def test_build_scenario_disables_write_posts():
    base = "https://budget.example.vn"
    reqs = [CapturedRequest(method="POST", url=f"{base}/api/budget/save", resource_type="xhr"),
            CapturedRequest(method="POST", url=f"{base}/api/budget/search", resource_type="xhr"),
            CapturedRequest(method="GET", url=f"{base}/api/budget/1", resource_type="xhr")]
    page = PageInfo(url=f"{base}/budget", title="Ngân sách", requests=reqs)
    p = Project(id="t", name="t", login=LoginConfig(base_url=base),
                modules=[ModuleInfo(name="Ngân sách", url=page.url, pages=[page])])
    sc = build_scenario(p, UCScore(uc_code="UC-1", uc_name="x", matched_pages=[page.url]))
    enabled = {s.url.rsplit("/", 1)[-1]: s.enabled for s in sc.steps if "/api/" in s.url}
    assert enabled == {"save": False, "search": True, "1": True}


# ---------------------------------------------------------------- 2. bộ chặn ghi không để lọt PUT/PATCH/DELETE

def test_guard_blocks_put_patch_delete_with_read_like_names():
    for m, u in [("DELETE", "https://x.vn/api/timesheet"), ("PUT", "https://x.vn/api/countries"),
                 ("PUT", "https://x.vn/api/user/layout"), ("PATCH", "https://x.vn/api/view-state"),
                 ("DELETE", "https://x.vn/api/getting-started")]:
        assert is_write_request(m, u), (m, u)
        assert is_write_request(m, u, level="strict"), (m, u)
        assert not is_write_request(m, u, level="off"), (m, u)


# ---------------------------------------------------------------- 3. thu số liệu máy chạy test (local)

def test_local_server_without_host_is_collected():
    cfg = MonitorConfig(enabled=True, tools=["perfmon"],
                        servers=[MonitorServer(name="Máy test", access="local", host=""),
                                 MonitorServer(name="SSH thiếu IP", access="ssh", host="")])
    assert [s.name for s in collector.live_targets(cfg)] == ["Máy test"]
    assert collector.has_live(cfg)


def test_local_stream_yields_samples_without_child_process():
    before = {c.pid for c in psutil.Process().children(recursive=True)}
    rows = list(sources.perfmon_stream(MonitorServer(access="local"), "", 1, lambda: False, samples=2))
    metrics = {m for _, m, _ in rows}
    assert {"cpu", "ram", "disk_read", "disk_write", "net"} <= metrics
    assert len([m for _, m, _ in rows if m == "cpu"]) == 2
    assert all(0 <= v <= 100 for _, m, v in rows if m in ("cpu", "ram", "disk_busy"))
    assert {c.pid for c in psutil.Process().children(recursive=True)} <= before     # không để lại typeperf


def test_local_stream_stops_quickly():
    t0 = time.monotonic()
    deadline = t0 + 1.2
    rows = list(sources.perfmon_stream(MonitorServer(access="local"), "", 60, lambda: time.monotonic() > deadline))
    assert rows == [] and time.monotonic() - t0 < 5


def test_stop_live_stops_gracefully_via_stop_file(tmp_path):
    stop_file = tmp_path / "collector.stop"
    code = ("import os,sys,time\n"
            f"f=r'{stop_file}'\n"
            "while not os.path.exists(f): time.sleep(0.2)\n"
            "sys.exit(0)\n")
    proc = subprocess.Popen([sys.executable, "-c", code])
    proc.perftool_stop_file = stop_file
    t0 = time.monotonic()
    runtime.stop_live(proc, lambda _m: None)
    assert proc.returncode == 0 and time.monotonic() - t0 < 10     # tự thoát, không bị kill
    assert not stop_file.exists()


# ---------------------------------------------------------------- 4. lượt chạy bị dừng giữa chừng

def test_interrupted_run_is_marked_stopped(tmp_path, monkeypatch):
    from perftool.runner.executor import load_run_file, save_run_file
    from perftool.ui import state
    from perftool.ui.steps import s07_run
    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "script.js").write_text("//")
    (d / "raw.csv").write_text("metric_name,timestamp\nhttp_req_duration,1\n")
    run = RunInfo(run_id="r1", tool="k6", status="running", started_at="2026-09-25 10:00:00",
                  script_file=str(d / "script.js"), raw_file=str(d / "raw.csv"))
    save_run_file(run)
    p = Project(id="t", name="t", runs=[run.model_copy()])
    monkeypatch.setattr(s07_run.jobs, "is_running", lambda *_a: False)
    monkeypatch.setattr(state, "save", lambda _p: None)
    s07_run.sync_runs(p)
    assert p.runs[0].status == "stopped" and p.runs[0].finished_at
    assert load_run_file(d).status == "stopped"


def test_jtl_truncated_and_unsorted(tmp_path):
    f = tmp_path / "results.jtl"
    f.write_text("timeStamp,elapsed,label,responseCode,success,bytes,allThreads\n"
                 "1005000,120,UC-1 :: A,200,true,10,50\n"
                 "1000000,100,UC-1 :: A,200,true,10,1\n"
                 "garbage-row\n"
                 "1003000,", encoding="utf-8")                     # dòng cuối bị cắt khi bấm Dừng
    df, vus = parse_jtl(f)
    assert len(df) == 2 and "threads" not in df.columns
    assert dict(zip(vus["ts"], vus["vus"])) == {1000.0: 1, 1005.0: 50}


# ---------------------------------------------------------------- các lỗi mức Trung bình / Thấp (đợt 2)

def test_session_token_not_embedded_when_login_per_vu(tmp_path):
    from perftool.models import AuthCapture, ScenarioStep, TestScenario
    from perftool.scriptgen.generator import generate_k6
    p = Project(id="t", name="t", login=LoginConfig(base_url="https://app.example.com"))
    p.auth = AuthCapture(login_url="https://app.example.com/api/login", login_content_type="application/json",
                         login_body_template='{"u":"{{USERNAME}}"}', static_headers={"Authorization": "Bearer SECRET-TOK"},
                         cookies=[{"name": "sid", "value": "SECRET-SID", "domain": "app.example.com"}])
    p.scenarios = [TestScenario(uc_code="UC-1", uc_name="x", steps=[ScenarioStep(name="a", url="https://app.example.com/")])]
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert "SECRET-TOK" not in js and "SECRET-SID" not in js
    p.test_config.auth_mode = "static_headers"
    js = generate_k6(p, p.scenarios, tmp_path / "s2.js").read_text(encoding="utf-8")
    assert "SECRET-TOK" in js and "SECRET-SID" in js          # chế độ này thật sự cần token


def test_chart_labels_distinguish_repeated_runs():
    from perftool.charts.charts import run_labels
    rows = [{"uc_code": "UC-1", "tool": "k6", "scenario_type": "smoke", "run_id": "20260925-124359_k6_smoke_UC-1"},
            {"uc_code": "UC-1", "tool": "k6", "scenario_type": "smoke", "run_id": "20260925-125058_k6_smoke_UC-1"},
            {"uc_code": "UC-2", "tool": "k6", "scenario_type": "smoke", "run_id": "20260925-125058_k6_smoke_UC-2"}]
    labels = run_labels(rows)
    assert len(set(labels)) == 3 and "12:43:59" in labels[0] and labels[2] == "UC-2 · k6 · Smoke Test"


def test_overview_counts_runs_not_rows():
    from perftool.results.insights import overview_insights
    rows = [{"run_id": rid, "uc_code": uc, "tool": "k6", "scenario_type": "smoke", "summary": {"samples": 10},
             "evaluation": {"passed": True, "reasons": []}, "p95_threshold": 3000, "config": {"vus": 3}}
            for rid in ("r1", "r2") for uc in ("UC-1", "UC-2", "UC-3")]
    first = overview_insights(rows)[0]
    assert first.startswith("Đã thực hiện 2 lượt kiểm thử, thu được 6 kết quả")
