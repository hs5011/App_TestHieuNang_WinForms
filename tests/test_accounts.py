"""Kiểm thử tính năng nhiều tài khoản kiểm thử."""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET

import pandas as pd

from perftool import accounts as acc
from perftool.models import AuthCapture, Project, RunInfo, ScenarioStep, TestScenario
from perftool.runner import executor
from perftool.scriptgen.generator import generate_jmeter, generate_k6


def test_parse_text_formats_and_header():
    text = "username,password\nuser01,P@ss,word;1\nuser02;abc\nuser03\tx y\n# ghi chú\n\nuser01,moi"
    got = dict(acc.parse_text(text))
    assert got == {"user01": "moi", "user02": "abc", "user03": "x y"}   # dòng sau ghi đè mật khẩu, bỏ tiêu đề


def test_parse_file_csv_and_excel(tmp_path):
    data = "Tên đăng nhập;Mật khẩu\nu1;p1\nu2;p2\n".encode("utf-8-sig")
    assert acc.parse_file(data, "a.csv") == [("u1", "p1"), ("u2", "p2")]
    x = tmp_path / "a.xlsx"
    pd.DataFrame({"Email": ["a@x.vn"], "Ghi chú": ["-"], "Password": ["q"]}).to_excel(x, index=False)
    assert acc.parse_file(x.read_bytes(), "a.xlsx") == [("a@x.vn", "q")]
    assert acc.parse_file(b"k1,v1\nk2,v2\n", "no_header.txt") == [("k1", "v1"), ("k2", "v2")]


def test_secret_files_written_and_cleaned(tmp_path):
    pool = [("u1", 'p,"1'), ("u2", "p2")]
    f1 = acc.write_secret_files(tmp_path, "k6", pool)[0]
    assert json.loads(f1.read_text(encoding="utf-8")) == [{"u": "u1", "p": 'p,"1'}, {"u": "u2", "p": "p2"}]
    f2 = acc.write_secret_files(tmp_path, "jmeter", pool)[0]
    assert list(csv.reader(open(f2, encoding="utf-8"))) == [["u1", 'p,"1'], ["u2", "p2"]]  # escape đúng dấu phẩy/nháy
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "secrets.properties").write_text("x")
    assert acc.cleanup_secret_files(tmp_path) == 3
    assert not list(tmp_path.rglob("accounts.*"))


def _project(mode: str) -> Project:
    p = Project(id="acc")
    p.login.base_url = "https://app.example.com"
    p.auth = AuthCapture(login_url="https://app.example.com/api/login", login_content_type="application/json",
                         login_body_template='{"u":"{{USERNAME}}","p":"{{PASSWORD}}"}', token_json_path="data.token")
    p.test_config.account_mode = mode
    p.scenarios = [TestScenario(uc_code="UC-1", uc_name="X", steps=[ScenarioStep(name="a", url="https://app.example.com/")])]
    return p


def test_templates_support_multi_accounts(tmp_path):
    p = _project("multi")
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert "open('./accounts.json')" in js and "(__VU - 1) % ACCOUNTS.length" in js
    root = ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx")).getroot()
    # mỗi VU lấy 1 tài khoản cố định (JSR223 đọc accounts.csv 1 lần + bộ đếm chung), không dùng CSV Data Set
    pre = list(root.iter("JSR223PreProcessor"))
    assert pre and not list(root.iter("CSVDataSet"))
    script = next(e.text for e in pre[0].iter("stringProp") if e.get("name") == "script")
    assert "accounts.csv" in script and "getAndIncrement() % lines.size()" in script and "${" not in script
    single = _project("single")
    root = ET.parse(generate_jmeter(single, single.scenarios, tmp_path / "p1.jmx")).getroot()
    assert not list(root.iter("JSR223PreProcessor"))       # 1 tài khoản -> không cần chọn tài khoản


def test_executor_removes_secret_files(tmp_path, monkeypatch):
    """Sau khi chạy (kể cả lỗi) không còn file tạm chứa mật khẩu."""
    (tmp_path / "script.js").write_text("//")
    run = RunInfo(run_id="r", tool="k6", script_file=str(tmp_path / "script.js"), raw_file=str(tmp_path / "raw.csv"),
                  log_file=str(tmp_path / "tool.log"), config={"account_mode": "multi"})
    seen = {}

    def fake_cmd(r, secrets):
        seen["files"] = sorted(f.name for f in tmp_path.iterdir())
        raise FileNotFoundError("k6 giả lập không tồn tại")

    monkeypatch.setattr(executor, "build_command", fake_cmd)
    try:
        executor.execute(run, [("u1", "p1"), ("u2", "p2")], log=lambda m: None)
    except FileNotFoundError:
        pass
    assert "accounts.json" in seen["files"]                 # có file khi đang chạy
    assert not (tmp_path / "accounts.json").exists()        # đã xoá sau khi chạy
