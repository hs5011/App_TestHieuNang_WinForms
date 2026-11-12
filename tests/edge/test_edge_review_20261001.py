"""Kiểm chứng các lỗi trong báo cáo review 2026-10-01 đã được sửa (mỗi test ghi số mục của báo cáo)."""
from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tokenize
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

from perftool import jobs, storage
from perftool.accounts import parse_file
from perftool.crawler.guard import get_has_write, is_query_request, is_write_request
from perftool.crawler.login_capture import login_template
from perftool.models import AuthCapture, Project, RunInfo, ScenarioStep, TestConfig, TestScenario
from perftool.results.parsers import parse_jtl, parse_k6_csv
from perftool.runner import executor
from perftool.scriptgen.generator import generate_jmeter, generate_k6
from perftool.scriptgen.profile import jmeter_blocks, parse_duration, stages_for, valid_duration
from perftool.uc_import import importer

ROOT = Path(__file__).resolve().parents[2]
J, F = "application/json", "application/x-www-form-urlencoded"


# ------------------------------------------------------------------ #1 GET có tác dụng phụ
@pytest.mark.parametrize("url", [
    "https://x.vn/api/hoso/seen?id=1", "https://x.vn/api/hoso/markread?id=1", "https://x.vn/ho-so/xu-ly?id=1",
    "https://x.vn/api/hoso/history?markSeen=1", "https://x.vn/api/task/delete/5",
    "https://x.vn/Handler.ashx?action=delete", "https://x.vn/VanBan/XoaVanBan?id=5"])
def test_get_with_side_effect_is_write(url):
    assert get_has_write(url)
    assert not is_query_request("GET", url)                 # bước 5: tắt mặc định
    assert is_write_request("GET", url, "xhr", "keyword")    # khi quét: bị chặn


@pytest.mark.parametrize("url", [
    "https://x.vn/api/list?sort=updatedDate&isDeleted=false", "https://x.vn/api/GetDanhSachChoXuLy",
    "https://x.vn/api/hoso/detail?id=1", "https://x.vn/signalr/negotiate?clientProtocol=1.5",
    "https://x.vn/api/services/app/Task/GetForEdit?id=3"])
def test_plain_get_reads_stay_enabled(url):
    assert is_query_request("GET", url) and not is_write_request("GET", url, "xhr")


def test_post_bare_view_is_not_query():
    assert not is_query_request("POST", "https://x.vn/api/hoso/view")
    assert is_query_request("POST", "https://x.vn/api/hoso/ViewDetail")


def test_get_document_not_blocked_by_guard():
    assert not is_write_request("GET", "https://x.vn/ho-so/xu-ly?id=1", "document")


def test_menu_link_on_record_excluded():
    from perftool.crawler.web_crawler import WebCrawler
    c = WebCrawler.__new__(WebCrawler)
    c.exclude, c.blacklist = [], []
    assert c._excluded("https://x.vn/ho-so/xu-ly?id=1", "Xử lý")
    assert not c._excluded("https://x.vn/HoSo/XuLy", "Xử lý hồ sơ")     # trang danh sách vẫn quét


# ------------------------------------------------------------------ #2 / #6 mẫu body đăng nhập
def test_manual_login_never_stores_password_or_otp():
    tpl, notes = login_template('{"username":"","password":"S3cret!Pass","otp":"123456"}', J, "", "")
    assert "S3cret" not in tpl and "123456" not in tpl
    assert json.loads(tpl) == {"username": "{{USERNAME}}", "password": "{{PASSWORD}}", "otp": ""} and notes


@pytest.mark.parametrize("body,ctype,user,pwd,expect", [
    ('{"username":"admin123","password":"admin"}', J, "admin123", "admin",
     '{"username":"{{USERNAME}}","password":"{{PASSWORD}}"}'),
    ('{"username":"admin","password":"admin"}', J, "admin", "admin",
     '{"username":"{{USERNAME}}","password":"{{PASSWORD}}"}'),
    ('{"username":"u1","password":"123","captchaId":"9123456"}', J, "u1", "123",
     '{"username":"{{USERNAME}}","password":"{{PASSWORD}}","captchaId":""}'),
    ("user=user01&pass=user01&remember=1", F, "user01", "user01", "user={{USERNAME}}&pass={{PASSWORD}}&remember=1"),
])
def test_login_template_by_field_name(body, ctype, user, pwd, expect):
    assert login_template(body, ctype, user, pwd)[0] == expect


def test_login_template_refuses_when_password_not_located():
    assert login_template('{"a":"x","b":"y"}', J, "", "")[0] is None
    assert login_template("pw=abc abc", "text/plain", "u", "abc")[0] is None     # mật khẩu xuất hiện 2 lần


def test_jmx_login_json_escapes_password(tmp_path):
    p = Project(id="t")
    p.login.base_url = "https://a.vn"
    p.auth = AuthCapture(login_url="https://a.vn/api/login", login_content_type=J, token_json_path="data.token",
                         login_body_template='{"username":"{{USERNAME}}","password":"{{PASSWORD}}"}')
    p.scenarios = [TestScenario(uc_code="UC-1", uc_name="A", steps=[ScenarioStep(name="s", url="https://a.vn/api/x")])]
    txt = generate_jmeter(p, p.scenarios, tmp_path / "p.jmx").read_text(encoding="utf-8")
    assert "JsonOutput.toJson(vars.get(&#39;PASSWORD&#39;)" in txt or "JsonOutput.toJson(vars.get('PASSWORD')" in txt
    root = ET.parse(tmp_path / "p.jmx").getroot()
    # token đăng nhập không còn gắn ở cấp Test Plan (áp lên cả request LOGIN)
    plan_hm = root.find("./hashTree/hashTree/HeaderManager")
    assert "AUTH_TOKEN" not in ET.tostring(plan_hm, encoding="unicode")
    assert "${AUTH_TOKEN}" in txt


# ------------------------------------------------------------------ #3 NaN think time
def test_nan_numbers_do_not_break_project(tmp_path):
    st_ = ScenarioStep(name="a", url="https://a.vn", think_time_s=float("nan"))
    assert st_.think_time_s == 0
    assert TestScenario(uc_code="U", uc_name="n", p95_threshold_ms=float("nan")).p95_threshold_ms is None
    assert TestConfig(think_time_s=None).think_time_s == 1.0
    p = Project(id="nan-proj")
    p.scenarios = [TestScenario(uc_code="U", uc_name="n", steps=[st_])]
    storage.save_project(p)
    f = storage.PROJECTS_DIR / "nan-proj" / "project.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["scenarios"][0]["steps"][0]["think_time_s"] = None          # file đã hỏng từ phiên bản cũ
    f.write_text(json.dumps(data), encoding="utf-8")
    assert [x.id for x in storage.list_projects()] == ["nan-proj"]
    generate_k6(storage.load_project("nan-proj"), p.scenarios, tmp_path / "s.js")


def test_df_steps_empty_cells():
    from perftool.ui.steps.s05_confirm import _df_steps
    df = pd.DataFrame([{"Tên bước": "a", "Method": "GET", "URL": "https://a.vn", "Think time (s)": float("nan"),
                        "Bật": None}])
    s = _df_steps(df)[0]
    assert s.think_time_s == 0 and s.enabled is False


def test_broken_project_is_reported():
    d = storage.PROJECTS_DIR / "broken"
    d.mkdir(parents=True)
    (d / "project.json").write_text("{not json", encoding="utf-8")
    assert storage.list_projects() == [] and "broken" in storage.BROKEN_PROJECTS


# ------------------------------------------------------------------ #4 demo_server chạy được trên Python 3.10/3.11
def _fstring_expr_backslash(path: Path) -> list[int]:
    """Dòng có dấu \\ trong PHẦN BIỂU THỨC của f-string (chỉ hợp lệ từ Python 3.12)."""
    bad, depth = [], 0
    with open(path, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            name = tokenize.tok_name[tok.type]
            if name == "FSTRING_START":
                depth += 1
            elif name == "FSTRING_END":
                depth -= 1
            elif depth and name == "STRING" and "\\" in tok.string:
                bad.append(tok.start[0])
    return bad


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python < 3.12 đã tự báo lỗi cú pháp khi biên dịch")
def test_no_backslash_in_fstring_expressions():
    files = list((ROOT / "samples").glob("*.py")) + list((ROOT / "perftool").rglob("*.py")) + [ROOT / "app.py"]
    bad = {str(f.relative_to(ROOT)): ln for f in files if (ln := _fstring_expr_backslash(f))}
    assert not bad


def test_samples_compile():
    import py_compile
    for f in (ROOT / "samples").glob("*.py"):
        py_compile.compile(str(f), doraise=True)


# ------------------------------------------------------------------ #5 mã thoát công cụ
def test_header_only_csv_is_not_data(tmp_path):
    raw = tmp_path / "raw.csv"
    raw.write_text("metric_name,timestamp,metric_value\n", encoding="utf-8")
    run = RunInfo(run_id="r", tool="k6", uc_code="U", scenario_type="load", script_file=str(tmp_path / "s.js"),
                  raw_file=str(raw), summary_file="", log_file="")
    assert not executor.has_fresh_data(run)
    raw.write_text("metric_name,timestamp,metric_value\nvus,1,1\n", encoding="utf-8")
    assert executor.has_fresh_data(run)


def test_tool_env_has_no_perftool_secrets(tmp_path, monkeypatch):
    seen = {}

    class P:
        pid, stdout = 1, iter(())

        def __init__(self, cmd, **kw):
            seen.update(kw["env"])

        def wait(self):
            return 0
    monkeypatch.setenv("PERFTOOL_ACCOUNTS", '[["a","secret"]]')
    monkeypatch.setenv("PERFTOOL_MONITOR_SECRETS", '{"x":"y"}')
    monkeypatch.setattr(executor.subprocess, "Popen", P)
    monkeypatch.setattr(executor, "build_command", lambda run, sec: ["k6"])
    run = RunInfo(run_id="r", tool="k6", uc_code="U", scenario_type="load", script_file=str(tmp_path / "s.js"),
                  raw_file=str(tmp_path / "raw.csv"), summary_file=str(tmp_path / "sum.json"),
                  log_file=str(tmp_path / "tool.log"))
    executor.execute(run, [("u", "p")], lambda m: None)
    assert seen["PERF_USERNAME"] == "u" and not any(k.startswith("PERFTOOL_") for k in seen)


# ------------------------------------------------------------------ #7 tái sử dụng PID
def test_reused_pid_is_not_our_job(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        st_ = {"pid": proc.pid, "status": "running", "create_time": jobs._create_time(proc.pid) - 1000}
        assert jobs._own_process(st_) is None                      # cùng PID nhưng không phải tiến trình đã khởi chạy
        st_["create_time"] = jobs._create_time(proc.pid)
        assert jobs._own_process(st_) is not None
        jobs.write_status("pid-proj", "j", **st_)
        st_["create_time"] -= 1000
        jobs.write_status("pid-proj", "j", **st_)
        jobs.stop_job("pid-proj", "j")
        assert proc.poll() is None                                # nút Dừng không diệt nhầm tiến trình khác
    finally:
        proc.kill()


# ------------------------------------------------------------------ #9 hồ sơ tải JMeter = k6
@pytest.mark.parametrize("typ", ["smoke", "load", "stress", "spike", "soak"])
def test_jmeter_blocks_follow_k6_profile(typ):
    cfg = TestConfig(scenario_type=typ, vus=50, duration="5m", ramp_up="1m", ramp_down="30s")
    st_ = stages_for(cfg)
    blocks = jmeter_blocks(st_)
    peak = max(s.target for s in st_)
    t_end = sum(s.duration_s for s in st_)
    for t in range(0, t_end, 5):     # số luồng tối đa có thể chạy tại t không vượt mức k6 ở cuối giai đoạn đó
        alive = sum(b.threads for b in blocks if b.delay_s <= t < b.delay_s + b.duration_s)
        assert alive <= peak
    assert sum(b.threads for b in blocks if b.delay_s == 0) >= 1
    if typ == "stress":
        assert [b.threads for b in blocks] == [25, 25, 12, 13] and sum(b.threads for b in blocks) == 75


def test_stress_with_one_vu_has_load_from_start():
    st_ = stages_for(TestConfig(scenario_type="stress", vus=1))
    assert all(s.target >= 1 for s in st_[:-1])


def test_jmx_stress_has_stepped_thread_groups(tmp_path):
    p = Project(id="t")
    p.test_config = TestConfig(scenario_type="stress", vus=50, tools=["jmeter"])
    p.scenarios = [TestScenario(uc_code="UC-1", uc_name="A", steps=[ScenarioStep(name="s", url="https://a.vn/x")])]
    root = ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx")).getroot()
    tgs = root.iter("ThreadGroup")
    got = [(int(t.find("stringProp[@name='ThreadGroup.num_threads']").text),
            int(t.find("stringProp[@name='ThreadGroup.delay']").text)) for t in tgs]
    assert len(got) == 4 and sum(n for n, _ in got) == 75 and got[0][1] == 0 and got[1][1] > 0


# ------------------------------------------------------------------ #10 mã UC trùng
def test_df_to_use_cases_dedupes_and_normalizes():
    df = pd.DataFrame([{"Mã UC": "UC-1", "Tên UC": "A"}, {"Mã UC": "UC-1", "Tên UC": "B"},
                       {"Mã UC": "UC-Đồ", "Tên UC": "Xóa"}])
    ucs = importer.df_to_use_cases(df, [])
    assert [u.code for u in ucs][:2] == ["UC-1", "UC-1 (2)"] and ucs[2].name == "Xóa"


def test_empty_sheet_message():
    buf = io.BytesIO()
    pd.DataFrame().to_excel(buf, index=False)
    buf.seek(0)
    with pytest.raises(ValueError, match="không có dữ liệu"):
        importer.read_table(buf, "a.xlsx")


def test_group_row_detected_with_ffill():
    df = pd.DataFrame({"STT": [None, "1", "2", None, "3"], "Phân hệ": ["Văn bản", None, None, None, None],
                       "Tên UC": [None, "Xem", "Sửa", None, "Tra cứu"]})
    df.loc[3, "STT"] = "2. Phân hệ Hồ sơ"
    ucs = importer.to_use_cases(df, {"name": "Tên UC", "module": "Phân hệ"}, ffill_module=True)
    assert [u.name for u in ucs] == ["Xem", "Sửa", "Tra cứu"]


# ------------------------------------------------------------------ #11 cache phân tích theo ngưỡng
def test_analysis_cache_invalidated_by_threshold(tmp_path):
    from perftool.results.analyzer import analyze_run
    raw = tmp_path / "results.jtl"
    raw.write_text("timeStamp,elapsed,label,responseCode,success,allThreads\n"
                   "1700000000000,500,UC-1 :: a,200,true,1\n1700000001000,700,UC-1 :: a,200,true,1\n", encoding="utf-8")
    run = RunInfo(run_id="r", tool="jmeter", uc_code="UC-1", scenario_type="load", script_file=str(tmp_path / "p.jmx"),
                  raw_file=str(raw), summary_file="", log_file="", config=TestConfig().model_dump())
    p = Project(id="t")
    p.scenarios = [TestScenario(uc_code="UC-1", uc_name="A", p95_threshold_ms=10000)]
    assert analyze_run(p, run)["ucs"]["UC-1"]["evaluation"]["passed"]
    p.scenarios[0].p95_threshold_ms = 100
    assert not analyze_run(p, run)["ucs"]["UC-1"]["evaluation"]["passed"]


# ------------------------------------------------------------------ #12 JTL dòng cụt
def test_truncated_jtl_row_is_dropped(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text("timeStamp,elapsed,label,responseCode,success,bytes,allThreads\n"
                 "1700000000000,120,UC-1 :: a,200,true,10,1\n1700000001000,130,UC-1 :: a", encoding="utf-8")
    df, _ = parse_jtl(f)
    assert len(df) == 1 and df["success"].all() and (df["uc"] == "UC-1").all()


# ------------------------------------------------------------------ mất phiên = lỗi
def test_k6_session_lost_marks_request_failed(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text("metric_name,timestamp,metric_value,expected_response,name,status,scenario\n"
                 "http_req_duration,100,50,true,UC-1 :: a,200,s\n"
                 "http_req_duration,101,60,true,UC-1 :: a,200,s\n"
                 "perftool_session_lost,101,1,,UC-1 :: a,,s\n", encoding="utf-8")
    df, _ = parse_k6_csv(f)
    assert df["success"].tolist() == [True, False] and df["status"].tolist()[1] == "SESSION_LOST"


def test_scripts_count_session_lost(tmp_path):
    p = Project(id="t")
    p.scenarios = [TestScenario(uc_code="UC 1,{x}", uc_name="A",
                                steps=[ScenarioStep(name="s", url="https://a.vn/api/x"),
                                       ScenarioStep(name="l", url="https://a.vn/Account/Login")])]
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert "perftool_session_lost" in js and "discardResponseBodies: true" in js
    assert "'http_req_duration{kind:step,uc_key:UC_1__x_}'" in js       # mã UC có , { } không phá threshold
    root = ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx")).getroot()
    names = [a.get("testname") for a in root.iter("ResponseAssertion")]
    assert sum("mất phiên" in (n or "") for n in names) == 1             # bước có URL đăng nhập không bị kiểm


# ------------------------------------------------------------------ lỗi mức thấp
@pytest.mark.parametrize("s,sec,ok", [("1m30", 0, False), ("-5m", 0, False), ("500ms", 1, True), ("0", 0, False),
                                      ("2m30s", 150, True), ("90", 90, True), ("30s\nimport x", 0, False)])
def test_parse_duration_strict(s, sec, ok):
    assert parse_duration(s) == sec and valid_duration(s) is ok


def test_accounts_file_delimiters():
    assert parse_file(b"username|password\nu1|a,b\nu2|x", "a.txt") == [("u1", "a,b"), ("u2", "x")]
    assert parse_file(b"user;pass\nu1;p;q", "a.csv") == [("u1", "p;q")]


def test_sql_readonly_blocks_side_effect_functions():
    from perftool.monitor.sources import check_readonly
    for q in ["select pg_read_binary_file('x')", "select * from dblink_exec('x')", "select pg_sleep(5)",
              "SELECT * FROM OPENROWSET('a')", "select setval('s', 1)"]:
        with pytest.raises(ValueError):
            check_readonly(q)


def test_no_nan_left(tmp_path):
    assert not math.isnan(TestConfig(p95_threshold_ms=float("nan")).p95_threshold_ms)
