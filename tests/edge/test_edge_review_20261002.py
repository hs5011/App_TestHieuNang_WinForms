"""Kiểm chứng các lỗi trong báo cáo review 2026-10-02 đã được sửa (mỗi test ghi số mục của báo cáo)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from perftool import storage
from perftool.analysis.complexity import build_scenario
from perftool.crawler.guard import classify, is_write_request, page_has_write
from perftool.crawler.login_capture import redact_body
from perftool.models import (AuthCapture, CapturedRequest, ModuleInfo, PageInfo, Project, ScenarioStep, TestScenario,
                             UCScore)
from perftool.results.parsers import parse_jtl
from perftool.runner import executor

B = "https://x.vn"


# ------------------------------------------------------------------ #1 whitelist đọc/ghi
@pytest.mark.parametrize("url", ["/api/notify/ack?id=1", "/api/tasks/touch?id=1", "/api/inbox/open?id=3",
                                 "/api/tasks/pin?id=1", "/api/TokenAuth/LogOut", "/api/hoso/seen?id=1"])
def test_side_effect_verbs_are_write(url):
    assert classify("GET", B + url) == "write"
    assert is_write_request("GET", B + url, "xhr")          # bị chặn cả khi quét


def test_unknown_record_get_is_disabled_not_read():
    assert classify("GET", B + "/api/tasks?id=1") == "unknown"


@pytest.mark.parametrize("url", [
    "/api/tasks?page=1&size=20", "/api/tasks/5", "/api/tasks/detail?id=1", "/api/departments",
    "/vpubtpgiao-viec/api/gia-han/danh-sach?NguoiGiaoViecNguoiDungId=3290&Page=1&TinhTrangGiaHan=DangGiaHan&isCreate=false",
    "/vpubtpgiao-viec/api/user/chiu-trach-nhiem-xu-ly", "/vpubtpgiao-viec/danh-muc/loai-xu-ly",
    "/vpubtpgiao-viec/danh-sach-cong-viec/370/danh-sach-bao-cao", "/assets/appconfig.production.json?d=1790321033832",
    "/api/services/app/Task/GetForEdit?id=3"])
def test_real_read_apis_stay_read(url):
    """Mẫu lấy từ dữ liệu quét thật: không API đọc nào bị tắt nhầm."""
    assert classify("GET", B + url) == "read"


@pytest.mark.parametrize("url,expect", [
    ("/inbox/read-all", True), ("/VanBan/XoaVanBan?id=5", True), ("/ho-so/xu-ly?id=1", True), ("/tasks/close?id=2", True),
    ("/HoSo/XuLy", False), ("/portal/vif/task/viec-can-xu-ly", False), ("/portal/vif/task/viec-cho-phe-duyet", False),
    ("/VanBan/ThemMoi", False), ("/HoSo/Edit/5", False), ("/portal/vif/task/viec-can-xu-ly/tien-do?congViecId=668", False),
    ("/portal/app/main/dynamicreport/report/viewer-utility/CBCC_App_OneSignal", False)])
def test_page_navigation_write(url, expect):
    assert page_has_write(B + url) is expect


def test_page_step_with_side_effect_disabled_and_post_view_blocked_on_load():
    p = Project(id="t")
    p.login.base_url = B
    pg = PageInfo(url=B + "/inbox/read-all", module="m", requests=[
        CapturedRequest(method="GET", url=B + "/api/inbox?page=1", resource_type="xhr"),
        CapturedRequest(method="GET", url=B + "/api/inbox/open?id=3", resource_type="xhr")])
    p.modules = [ModuleInfo(name="m", url=pg.url, pages=[pg])]
    sc = build_scenario(p, UCScore(uc_code="U", uc_name="u", matched_pages=[pg.url]))
    assert [(s.url.replace(B, ""), s.enabled) for s in sc.steps] == [
        ("/inbox/read-all", False), ("/api/inbox?page=1", True), ("/api/inbox/open?id=3", False)]
    # tải trang mặc định mức strict: POST ghi nhận lượt xem không được đi qua
    assert is_write_request("POST", B + "/api/log/visit", "xhr", "strict")
    assert is_write_request("POST", B + "/api/hoso/view", "xhr", "strict")


# ------------------------------------------------------------------ #2 mật khẩu trong request ghi được
def test_password_in_captured_body_is_redacted():
    r = CapturedRequest(method="POST", url=B + "/api/auth/login", resource_type="xhr",
                        post_data='{"username":"user01","password":"Test@123"}')
    assert "Test@123" not in r.post_data and r.sensitive
    # form đổi mật khẩu, OTP
    body, hit = redact_body("old_password=a1&new_password=b2&otp=999&note=x")
    assert hit and "a1" not in body and "b2" not in body and "999" not in body and "note=x" in body
    # mật khẩu đã biết nằm trong trường tên lạ
    assert redact_body('{"mk":"Test@123","q":1}', ["Test@123"]) == ('{"mk":"***","q":1}', True)
    # body bình thường giữ nguyên từng ký tự
    assert redact_body('{"keyword": "", "page": 1}') == ('{"keyword": "", "page": 1}', False)


def test_sensitive_request_never_enabled_and_session_headers_dropped():
    p = Project(id="t")
    p.login.base_url = B
    pg = PageInfo(url=B + "/p", module="m", requests=[
        CapturedRequest(method="POST", url=B + "/api/user/GetProfile", resource_type="xhr",
                        post_data='{"password":"x1y2z3"}', headers={"x-csrf-token": "abc", "x-api-key": "k"})])
    assert pg.requests[0].headers == {"x-api-key": "k"}
    p.modules = [ModuleInfo(name="m", url=pg.url, pages=[pg])]
    sc = build_scenario(p, UCScore(uc_code="U", uc_name="u", matched_pages=[pg.url]))
    assert not sc.steps[-1].enabled and "x1y2z3" not in sc.steps[-1].body


def test_old_project_json_with_password_is_cleaned_on_load():
    p = Project(id="old")
    storage.save_project(p)
    f = storage.PROJECTS_DIR / "old" / "project.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["modules"] = [{"name": "m", "url": "u", "pages": [{"url": "u", "module": "m", "requests": [
        {"method": "POST", "url": "u", "post_data": '{"password":"Secret#1"}'}]}]}]
    f.write_text(json.dumps(d), encoding="utf-8")
    storage.save_project(storage.load_project("old"))
    assert "Secret#1" not in f.read_text(encoding="utf-8")


# ------------------------------------------------------------------ #3 mã UC có ký tự cấm của Windows
def test_run_dir_safe_for_windows():
    p = Project(id="win")
    p.test_config.tools = ["k6"]
    p.scenarios = [TestScenario(uc_code=c, uc_name="a", steps=[ScenarioStep(name="s", url=B + "/x")])
                   for c in ["UC:01", "UC*01", 'A"B', "UC 1.2: Tra cứu", "UC-1."]]
    runs = executor.plan_runs(p)
    dirs = [Path(r.script_file).parent for r in runs]
    assert all(d.is_dir() for d in dirs) and len({d.name for d in dirs}) == 5
    assert [r.uc_code for r in runs] == ["UC:01", "UC*01", 'A"B', "UC 1.2: Tra cứu", "UC-1."]   # mã gốc giữ nguyên


# ------------------------------------------------------------------ #4 dương tính giả GET-ghi
@pytest.mark.parametrize("url", ["/api/tasks/extend-info?id=1", "/api/change-log?id=3", "/api/mail/send-history?id=2"])
def test_noun_compound_get_is_read(url):
    assert classify("GET", B + url) == "read" and not is_write_request("GET", B + url, "xhr")


# ------------------------------------------------------------------ #5 cookie / Authorization chỉ ở auth.json
def test_session_secrets_not_in_project_json():
    p = Project(id="sess")
    p.auth = AuthCapture(login_url=B + "/login", cookies=[{"name": "sid", "value": "SECRETCOOKIE"}],
                         static_headers={"Authorization": "Bearer SECRETTOKEN"})
    storage.save_project(p)
    txt = (storage.PROJECTS_DIR / "sess" / "project.json").read_text(encoding="utf-8")
    assert "SECRETCOOKIE" not in txt and "SECRETTOKEN" not in txt
    # dự án cũ còn giữ trong project.json -> chuyển sang auth.json, vẫn dùng được cho chế độ cookie/token tĩnh
    d = json.loads(txt)
    d["auth"].update(cookies=[{"name": "sid", "value": "SECRETCOOKIE"}], static_headers={"Authorization": "Bearer T"})
    (storage.PROJECTS_DIR / "sess" / "project.json").write_text(json.dumps(d), encoding="utf-8")
    q = storage.load_project("sess")
    assert q.auth.cookies[0]["value"] == "SECRETCOOKIE" and q.auth.static_headers["Authorization"] == "Bearer T"
    assert "SECRETCOOKIE" in storage.auth_file("sess").read_text(encoding="utf-8")
    storage.save_project(q)
    assert "SECRETCOOKIE" not in (storage.PROJECTS_DIR / "sess" / "project.json").read_text(encoding="utf-8")


# ------------------------------------------------------------------ #6 TLS / SSH mặc định an toàn
def test_tls_verify_on_by_default(tmp_path):
    from perftool import config
    from perftool.scriptgen.generator import generate_k6
    p = Project(id="tls")
    p.scenarios = [TestScenario(uc_code="U", uc_name="u", steps=[ScenarioStep(name="s", url=B + "/x")])]
    assert not config.insecure_tls(p.login)
    assert "insecureSkipTLSVerify: false" in generate_k6(p, p.scenarios, tmp_path / "a.js").read_text(encoding="utf-8")
    p.login.skip_tls_verify = True
    assert "insecureSkipTLSVerify: true" in generate_k6(p, p.scenarios, tmp_path / "b.js").read_text(encoding="utf-8")
    assert config.get("security.ssh_auto_add_host") is False


def test_ssh_unknown_host_rejected_without_explicit_trust(monkeypatch):
    import paramiko

    from perftool.models import MonitorServer
    from perftool.monitor import sources
    monkeypatch.setattr(sources, "known_hosts_file", lambda: Path(tempfile.mkdtemp()) / "known_hosts")
    seen = {}

    def fake_connect(self, *a, **k):
        seen["policy"] = type(self._policy).__name__
    monkeypatch.setattr(paramiko.SSHClient, "connect", fake_connect)
    sources._ssh_client(MonitorServer(host="10.0.0.5", ssh_user="u"), "pw")
    assert seen["policy"] == "RejectPolicy"
    sources._ssh_client(MonitorServer(host="10.0.0.5", ssh_user="u"), "pw", trust_new=True)
    assert seen["policy"] == "AutoAddPolicy"


# ------------------------------------------------------------------ Thấp: JMeter mất phiên cùng mã với k6
def test_jtl_session_lost_status(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text("timeStamp,elapsed,label,responseCode,success,failureMessage\n"
                 "1700000000000,10,U :: a,200,false,SESSION_LOST: bị chuyển về trang đăng nhập\n"
                 "1700000001000,10,U :: a,500,false,\n", encoding="utf-8")
    df, _ = parse_jtl(f)
    assert df["status"].tolist() == ["SESSION_LOST", "500"]
