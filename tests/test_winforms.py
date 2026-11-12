"""Test hỗ trợ ứng dụng WinForms (ghi lưu lượng API -> màn hình -> dự án). Không dùng mạng ngoài."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import perftool.storage as storage  # noqa: E402
from perftool.crawler import winforms as wf  # noqa: E402
from perftool.crawler.winforms_merge import merge_winforms, rename_segments  # noqa: E402
from perftool.models import Project  # noqa: E402

BASE = "https://api.test.local"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PROJECTS_DIR", tmp_path / "projects")


def _entry(t, method, path, status=200, body=None, resp=None, ctype="application/json"):
    e = {"startedDateTime": f"2026-11-12T08:00:{t:02d}.000Z", "time": 40.0,
         "request": {"method": method, "url": BASE + path,
                     "headers": [{"name": "Content-Type", "value": "application/json"},
                                 {"name": "Authorization", "value": "Bearer abcdefghijklmnopqrstuvwxyz"},
                                 {"name": "X-Client", "value": "winforms"}]},
         "response": {"status": status, "content": {"mimeType": ctype, "size": 100,
                                                    "text": json.dumps(resp) if resp is not None else ""}}}
    if body is not None:
        e["request"]["postData"] = {"mimeType": "application/json", "text": json.dumps(body)}
    return e


def make_har():
    items = [{"id": i, "name": f"n{i}"} for i in range(30)]
    return {"log": {"entries": [
        _entry(0, "POST", "/api/auth/login", body={"username": "u1", "password": "Secret@123"},
               resp={"data": {"accessToken": "tok" * 12}}),
        # màn hình 1: danh sách hồ sơ (đọc)
        _entry(10, "GET", "/api/hoso/getall?page=1&keyword=a", resp={"data": items}),
        _entry(11, "GET", "/api/danhmuc/loai", resp={"data": items[:5]}),
        # nghỉ 20 giây -> màn hình 2: thêm mới hồ sơ (ghi)
        _entry(40, "POST", "/api/hoso/create", body={"ten": "x", "loai": 1, "ghichu": "g", "ngay": "2026-01-01"},
               resp={"ok": True}),
        _entry(41, "GET", "/static/logo.png", ctype="image/png"),
    ]}}


def test_har_parse_and_login_template():
    flows = wf.flows_from_har(make_har(), ["Secret@123"], "u1", "Secret@123")
    assert len(flows) == 5
    login = [f for f in flows if f.get("login")]
    assert len(login) == 1 and "Secret@123" not in json.dumps(flows)       # mật khẩu không lọt vào dữ liệu
    assert "{{PASSWORD}}" in login[0]["login"]["template"]
    assert login[0]["login"]["token_path"] == "data.accessToken"
    auth = wf.extract_auth(flows)
    assert auth.login_url.endswith("/api/auth/login") and auth.token_json_path == "data.accessToken"
    assert auth.static_headers["Authorization"].startswith("Bearer ")      # chỉ trong bộ nhớ


def test_split_by_gap_and_stats():
    flows = wf.flows_from_har(make_har(), ["Secret@123"], "u1", "Secret@123")
    groups = wf.split_screens(flows, None, gap_s=8)
    assert len(groups) == 3          # đăng nhập | danh sách | thêm mới (logo.png bị loại)
    sess = wf.build_sessions(groups)
    segs = [s for x in sess for s in x["segments"]]
    assert len(segs) == 2            # request đăng nhập không thành màn hình
    lst = next(s for s in segs if s["stats"]["tables"])
    assert lst["stats"]["table_rows"] == 30 and lst["stats"]["selects"] == 1 and lst["stats"]["inputs"] >= 1
    add = next(s for s in segs if s["stats"]["forms"])
    assert add["stats"]["inputs"] == 4 and "Thêm mới" in add["stats"]["crud"]


def test_markers_override_gap():
    flows = wf.flows_from_har(make_har())
    t0 = flows[1]["t"]
    marks = [{"t": t0 - 1, "screen": "Danh sách hồ sơ", "module": "Hồ sơ"},
             {"t": flows[3]["t"] - 1, "screen": "Thêm hồ sơ", "module": "Hồ sơ"}]
    groups = wf.split_screens(flows, marks)
    assert [g["screen"] for g in groups] == ["Danh sách hồ sơ", "Thêm hồ sơ"]
    assert {g["module"] for g in groups} == {"Hồ sơ"}


def test_merge_into_project_and_rename(tmp_path):
    p = Project(id="wf1")
    p.kind = "winforms"
    storage.save_project(p)
    flows = wf.flows_from_har(make_har(), ["Secret@123"], "u1", "Secret@123")
    work = storage.sub_dir(p.id, "crawl")
    marks = [{"t": flows[1]["t"] - 1, "screen": "Danh sách hồ sơ", "module": "Hồ sơ"},
             {"t": flows[3]["t"] - 1, "screen": "Thêm hồ sơ", "module": "Hồ sơ"}]
    sess = wf.build_sessions(wf.split_screens(flows, marks))
    wf.append_record(work / "record.json", sess)
    n = merge_winforms(p)
    assert n == 2
    names = [pg.title for pg in p.all_pages()]
    assert names == ["Danh sách hồ sơ", "Thêm hồ sơ"]
    assert all(pg.api_requests for pg in p.all_pages())
    assert [m.name for m in p.modules] == ["Hồ sơ"]
    # đổi tên rồi gộp lại: không nhân đôi màn hình
    started = sess[0]["started"]
    assert rename_segments(work / "record.json", [{"session": started, "index": 1, "title": "Tạo mới hồ sơ"}]) == 1
    merge_winforms(p)
    assert [pg.title for pg in p.all_pages()] == ["Danh sách hồ sơ", "Tạo mới hồ sơ"]
    # request ghi KHÔNG được bật sẵn trong kịch bản (an toàn)
    from perftool.crawler.guard import is_query_request
    add = p.all_pages()[1]
    assert not any(is_query_request(r.method, r.url) for r in add.requests if r.method == "POST")


def test_addon_guard_blocks_writes():
    from perftool.crawler.guard import is_write_request
    assert is_write_request("POST", BASE + "/api/hoso/create", "xhr", "keyword")
    assert is_write_request("DELETE", BASE + "/api/hoso/5", "xhr", "keyword")
    assert not is_write_request("POST", BASE + "/api/hoso/getall", "xhr", "keyword")


def test_downstream_scoring_and_scripts(tmp_path):
    """Màn hình WinForms đi hết đường: chấm điểm UC -> kịch bản -> script k6/JMeter (request ghi mặc định tắt)."""
    from perftool.analysis import complexity as cx
    from perftool.models import UseCase
    from perftool.scriptgen.generator import generate_jmeter, generate_k6
    p = Project(id="wf2")
    p.kind = "winforms"
    p.login.base_url = BASE
    p.use_cases = [UseCase(code="UC-1", name="Danh sách hồ sơ", module="Hồ sơ", description="Xem danh sách, tìm kiếm hồ sơ"),
                   UseCase(code="UC-2", name="Thêm hồ sơ", module="Hồ sơ", description="Thêm mới hồ sơ, nhập thông tin")]
    storage.save_project(p)
    flows = wf.flows_from_har(make_har(), ["Secret@123"], "u1", "Secret@123")
    marks = [{"t": flows[1]["t"] - 1, "screen": "Danh sách hồ sơ", "module": "Hồ sơ"},
             {"t": flows[3]["t"] - 1, "screen": "Thêm hồ sơ", "module": "Hồ sơ"}]
    wf.append_record(storage.sub_dir(p.id, "crawl") / "record.json", wf.build_sessions(wf.split_screens(flows, marks)))
    merge_winforms(p)
    p.auth = wf.extract_auth(flows)
    scores = cx.recommend(cx.score_project(p), top_n=2)
    assert {s.uc_code for s in scores if s.matched_pages} == {"UC-1", "UC-2"}
    p.scenarios = [cx.build_scenario(p, s) for s in scores]
    steps = [st for sc in p.scenarios for st in sc.steps]
    assert steps and all(st.url.startswith(BASE) for st in steps)
    assert all(not st.enabled for st in steps if st.method == "POST" and "create" in st.url)
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert "api/hoso/getall" in js and "{{PASSWORD}}" in js
    assert generate_jmeter(p, p.scenarios, tmp_path / "p.jmx").exists()
