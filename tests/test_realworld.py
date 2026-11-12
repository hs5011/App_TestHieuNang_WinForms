"""Các trường hợp phát hiện khi chạy trên hệ thống thật (Angular/ABP, mẫu UC QĐ 671)."""
from __future__ import annotations

import io

from perftool.analysis import complexity as cx
from perftool.models import CapturedRequest, ModuleInfo, PageInfo, Project, UCScore, UseCase
from perftool.scriptgen.generator import generate_jmeter, generate_k6
from perftool.scriptgen.profile import stages_for
from perftool.uc_import import importer

PL3_CSV = """STT,,Tên Use case,Tên tác nhân,Giao dịch (Transaction),Phân loại theo độ phức tạp
A. HỆ THỐNG ĐIỀU HÀNH,,,,,
I. Nhóm phân hệ nghiệp vụ,,,,,
1. Quản lý công việc,,,,,
1,1,Thống kê giao việc theo phòng ban,Lãnh đạo,,Phức tạp
,1,,,Xem thống kê theo phòng,
,1,,,Lọc theo thời gian,
,1,,,Xuất Excel,
2,2,Xem danh sách công việc,Chuyên viên,,Trung bình
,2,,,Xem danh sách,
2. Danh bạ,,,,,
3,3,Tra cứu danh bạ nội bộ,Tất cả,,Đơn giản
"""


def test_import_hierarchical_pl3():
    df = importer.read_table(io.BytesIO(PL3_CSV.encode("utf-8")), "uc.csv")
    m = importer.guess_mapping(list(df.columns))
    assert m["name"] == "Tên Use case" and "transaction" in m
    ucs = importer.to_use_cases(df, m)
    assert [u.code for u in ucs] == ["UC-001", "UC-002", "UC-003"]
    assert ucs[0].module == "Quản lý công việc" and ucs[2].module == "Danh bạ"
    assert ucs[0].steps == 3 and ucs[0].complexity == "Phức tạp"
    assert ucs[0].extra["system"] == "HỆ THỐNG ĐIỀU HÀNH" and ucs[0].extra["group"] == "Nhóm phân hệ nghiệp vụ"


def _req(url, method="GET", **kw):
    return CapturedRequest(method=method, url=url, resource_type="xhr", status=200, duration_ms=50, **kw)


SHELL = [_req(f"https://app.example.com:9412/api/shell/{n}") for n in ("session", "menu", "config")]


def _page(url, menu, extra):
    return PageInfo(url=url, menu_text=menu, title="CHÍNH QUYỀN SỐ", module=menu, requests=SHELL + extra)


def _project() -> Project:
    p = Project(id="rw")
    p.login.base_url = "https://app.example.com"
    task = _page("https://app.example.com/task", "Giao việc", [
        _req("https://app.example.com:9412/api/GetToken", "POST", token_path="result.data.accessToken",
             token_sig="abc123", post_data="{}"),
        _req("https://mobile.example.com/giao-viec/thong-ke", auth_sig="abc123", headers={"orion": "Rigel x"}),
    ])
    contact = _page("https://app.example.com/contact", "Danh Bạ", [_req("https://app.example.com:9412/api/contact/list")])
    news = _page("https://app.example.com/news", "Tin Tức", [_req("https://app.example.com:9412/api/news/list")])
    notif = _page("https://app.example.com/notif", "Thông Báo", [_req("https://app.example.com:9412/api/notif/list")])
    p.modules = [ModuleInfo(name=pg.menu_text, url=pg.url, pages=[pg]) for pg in (task, contact, news, notif)]
    p.use_cases = [UseCase(code="UC-1", name="Thống kê giao việc theo phòng ban", steps=5, complexity="Phức tạp"),
                   UseCase(code="UC-2", name="Tra cứu danh bạ nội bộ", steps=3),
                   UseCase(code="UC-3", name="Tổng quan văn bản cần xử lý", steps=4)]
    return p


def test_common_endpoints_brand_words_and_port():
    p = _project()
    pages = p.all_pages()
    common = cx.common_endpoints(pages)
    assert len(common) == 3                               # API khung bị loại
    assert {"chinh", "quyen"} <= cx.common_page_words(pages)  # tên thương hiệu trong tiêu đề bị loại
    reqs = cx.relevant_requests([pages[1]], p.login.base_url, common)
    assert [r.url for r in reqs] == ["https://app.example.com:9412/api/contact/list"]  # cổng khác vẫn giữ


def test_matching_is_not_overly_permissive():
    scores = {s.uc_code: s for s in cx.score_project(_project())}
    assert scores["UC-1"].matched_pages == ["https://app.example.com/task"]
    assert scores["UC-2"].matched_pages == ["https://app.example.com/contact"]
    assert scores["UC-3"].matched_pages == []             # hệ thống ngoài -> không ghép bừa


def test_token_chain_and_custom_headers(tmp_path):
    p = _project()
    s = next(x for x in cx.score_project(p) if x.uc_code == "UC-1")
    sc = cx.build_scenario(p, s)
    tok = next(x for x in sc.steps if "GetToken" in x.url)
    mob = next(x for x in sc.steps if "mobile" in x.url)
    assert tok.extract_token == "result.data.accessToken" and tok.token_var == "TOKEN_1" and tok.enabled
    assert mob.use_token == "TOKEN_1" and mob.headers == {"orion": "Rigel x"}
    p.scenarios = [sc]
    js = generate_k6(p, [sc], tmp_path / "s.js").read_text(encoding="utf-8")
    assert '"use": "TOKEN_1"' in js and '"orion": "Rigel x"' in js
    jmx = generate_jmeter(p, [sc], tmp_path / "p.jmx").read_text(encoding="utf-8")
    assert "Bearer ${TOKEN_1}" in jmx and "$.result.data.accessToken" in jmx and ">orion<" in jmx


def test_recommend_diversity():
    same = ["GET a/x", "GET a/y", "POST a/z"]
    sc = [UCScore(uc_code="A", uc_name="A", total=60, matched_pages=["p1"], api_paths=same),
          UCScore(uc_code="B", uc_name="B", total=59, matched_pages=["p2"], api_paths=same),   # cùng bộ API
          UCScore(uc_code="C", uc_name="C", total=58, matched_pages=["p1"], api_paths=["GET q"]),  # cùng trang
          UCScore(uc_code="D", uc_name="D", total=57, matched_pages=["p3"], api_paths=[]),     # không có API
          UCScore(uc_code="E", uc_name="E", total=50, matched_pages=["p4"], api_paths=["GET m/n"])]
    picked = [s.uc_code for s in cx.recommend(sc, 3) if s.recommended]
    assert picked == ["A", "E"]


def test_smoke_profile_holds_constant_load():
    p = Project(id="x")
    p.test_config.scenario_type, p.test_config.duration = "smoke", "1m"
    st = stages_for(p.test_config, 13)
    assert st[0].duration_s <= 5 and st[0].target == 13 and st[1].target == 13


def test_operation_groups_and_auto_caps():
    """CRUD/Xử lý dữ liệu đếm LOẠI thao tác (không đếm lặp); mức trần tự động trải điểm 0-1."""
    text = "Thêm mới, thêm, thêm nữa công việc; sửa; tìm kiếm, tìm kiếm, lọc danh sách và xuất Excel"
    assert sorted(cx.matched_groups(text, "crud")) == ["sua", "them_moi"]
    assert sorted(cx.matched_groups(text, "data_processing")) == ["danh_sach", "tim_kiem", "xuat_nhap"]
    raws = [{"steps": s, "screens": 1, "api_count": a, "crud": 1, "data_processing": 2, "response_time": 100}
            for s, a in ((2, 1), (4, 5), (6, 10), (8, 20), (30, 40))]
    caps = cx.compute_caps(raws, "auto")
    assert caps["steps"] == 8 and caps["api_count"] == 20        # p90, không bị kéo bởi giá trị ngoại lai (30, 40)
    assert caps["screens"] == 2 and caps["response_time"] == 300  # không nhỏ hơn mức sàn
    assert cx.compute_caps(raws, "fixed")["steps"] == 15
    sc = cx.normalize(raws[1], caps)
    assert sc["steps"] == 0.5 and sc["api_count"] == 0.25
    contrib = cx.contributions(sc, {k: 1 for k in cx.CRITERIA})
    assert round(sum(contrib.values()), 1) == cx.total_score(sc, {k: 1 for k in cx.CRITERIA})


def test_score_project_records_caps():
    p = _project()
    cx.score_project(p, caps_mode="auto")
    assert set(p.score_caps) == set(cx.CRITERIA)


def test_scenario_display_names():
    from perftool.scriptgen.profile import SCENARIO_TYPES, scenario_name
    assert [scenario_name(k) for k in SCENARIO_TYPES] == ["Smoke Test", "Load Test", "Stress Test", "Spike Test",
                                                          "Soak Test"]
    assert scenario_name("custom") == "custom"


def _row(typ, passed, samples, errors, reasons, codes=None, ts_file=""):
    return {"uc_code": "UC-1", "tool": "k6", "scenario_type": typ, "ts_file": ts_file,
            "summary": {"samples": samples, "errors": errors, "status_codes": codes or {}, "p95": 100},
            "evaluation": {"passed": passed, "p95_ok": True, "err_ok": passed, "reasons": reasons}}


def test_smoke_fails_but_load_passes(tmp_path):
    from perftool.results.insights import recommendations, smoke_inconsistencies
    ts = tmp_path / "ts.csv"       # lỗi dồn ở đầu phiên -> khởi động nguội
    ts.write_text("t,rps,avg,p95,error_rate,errors,vus\n0,5,100,200,0.5,4,5\n5,5,90,150,0,0,5\n"
                  "10,5,80,120,0,0,5\n20,5,80,120,0,0,5\n30,5,80,120,0.1,1,5\n")
    rows = [_row("smoke", False, 300, 5, ["tỷ lệ lỗi 1.67% > ngưỡng 1.00%"], {"500": 5}, str(ts)),
            _row("load", True, 30000, 5, [])]
    notes = smoke_inconsistencies(rows)
    assert len(notes) == 1 and "Smoke Test KHÔNG đạt" in notes[0] and "Load Test đạt" in notes[0]
    assert "mẫu nhỏ" in notes[0] and "khởi động nguội" in notes[0]
    assert any("chạy lại Smoke Test" in r for r in recommendations(
        [{**r, "summary": {**r["summary"], "max_vus": 5, "error_rate": 0.01}} for r in rows]))
    rows[1]["evaluation"]["passed"] = False              # cả hai cùng không đạt -> không phải trường hợp này
    assert smoke_inconsistencies(rows) == []


def test_recommend_up_to_k_per_module():
    def sc(code, mod, total, page, apis):
        return UCScore(uc_code=code, uc_name=code, module=mod, total=total, matched_pages=[page], api_paths=apis)
    scores = [sc("A1", "A", 90, "pa1", ["x1"]), sc("A2", "A", 80, "pa2", ["x2"]), sc("A3", "A", 70, "pa3", ["x3"]),
              sc("B1", "B", 60, "pb1", ["y1"]), sc("B2", "B", 50, "pb1", ["y2"]),      # B2 trùng màn hình với B1
              sc("C1", "C", 40, "pc1", ["z1"])]
    picked = [s.uc_code for s in cx.recommend(scores, top_n=1, per_module=2) if s.recommended]
    assert picked == ["A1", "A2", "B1", "C1"]
    assert [s.uc_code for s in cx.recommend(scores, top_n=1, per_module=True) if s.recommended] == ["A1", "B1", "C1"]
    assert [s.uc_code for s in cx.recommend(scores, top_n=2) if s.recommended] == ["A1", "A2"]


def test_popups_count_as_screens():
    from perftool.models import PopupInfo
    p = _project()
    task = p.modules[0].pages[0]
    task.popups = [PopupInfo(trigger="thêm mới", title="Thêm mới", inputs=4),
                   PopupInfo(trigger="xem chi tiết", title="Chi tiết", tables=1),
                   PopupInfo(trigger="thêm", title="Menu", buttons=3, counted=False)]   # menu thả xuống: không tính
    raw = cx.compute_raw(p.use_cases[0], [task], p.login.base_url)
    assert raw["screens"] == 3                      # 1 trang + 2 popup được tính
