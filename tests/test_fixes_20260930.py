"""Hồi quy các lỗi mức Cao / Trung bình tìm được bởi đợt kiểm thử 5 agent (2026-09-30)."""
import json

import pytest

from perftool.analysis import complexity as cx
from perftool.crawler.guard import has_write_keyword, is_query_request, is_write_request
from perftool.crawler.interactions import host_key, is_banned, join_url, page_key
from perftool.models import (CapturedRequest, LoginConfig, ModuleInfo, PageInfo, Project, TestConfig, UCScore,
                             UseCase)
from perftool import config

API = "https://x.vn/api"


# ------------------------------------------------------------------ bộ chặn ghi
@pytest.mark.parametrize("url", [
    f"{API}/tasklist/delete/5", f"{API}/search/delete", f"{API}/checklist/remove/12", f"{API}/detail/update/7",
    f"{API}/report/delete/3", "https://x.vn/Handler.ashx?action=delete&id=5", f"{API}/adv/cap-nhat-trang-thai",
    f"{API}/van-ban/trinh-ky", f"{API}/adv/trinh-lanh-dao", f"{API}/ho-so/gui", f"{API}/Task/CheckAndApprove",
    f"{API}/cv/5/read", f"{API}/TaskList/Remove", f"{API}/SavedSearch/Delete", f"{API}/TaskDetail/Update",
    f"{API}/delete/items", f"{API}/HuyVanBan", f"{API}/cv/chuyen-xu-ly", f"{API}/x?cmd=xoabanghi",
])
def test_write_urls_blocked_everywhere(url):
    """Có từ ghi ở đoạn cuối / query -> chặn ở mức keyword VÀ strict, không bao giờ được bật sẵn trong kịch bản."""
    assert is_write_request("POST", url), url
    assert is_write_request("POST", url, level="strict"), url
    assert not is_query_request("POST", url), url


@pytest.mark.parametrize("url", [
    f"{API}/Task/GetAll", f"{API}/Task/GetForEdit", f"{API}/tasks/search", f"{API}/DRViewer/PostData",
    f"{API}/Report/ExecuteStoreWithParam", "https://x.vn/notify/negotiate", f"{API}/VPUB/DEV/CCHC_HCM_Get_ThongBao",
    f"{API}/huyen/GetAll", f"{API}/guide/getall",
    f"{API}/DRViewer/PostData?UrlPage=%2Fportal%2Freport%2FCBCC_App_OneSignal&ispopup=false",
])
def test_query_urls_still_enabled(url):
    assert is_query_request("POST", url), url
    assert not is_write_request("POST", url), url


def test_short_keywords_whole_word_only():
    assert not has_write_keyword(f"{API}/Item/GetById?guid=abc")       # "gui" ≠ guid
    assert not has_write_keyword(f"{API}/thread/list")                   # "=read" ≠ thread
    assert has_write_keyword(f"{API}/notification/MarkRead")


def test_unknown_post_keyword_passes_strict_blocks():
    assert not is_write_request("POST", f"{API}/tasks", level="keyword")
    assert is_write_request("POST", f"{API}/tasks", level="strict")
    assert not is_query_request("POST", f"{API}/tasks")


def test_build_scenario_disables_blocked_requests():
    p = Project(id="t")
    p.login = LoginConfig(base_url="https://x.vn")
    blocked = CapturedRequest(method="POST", url=f"{API}/Task/GetList", resource_type="xhr", blocked=True)
    ok = CapturedRequest(method="POST", url=f"{API}/Task/GetAll", resource_type="xhr")
    pg = PageInfo(url="https://x.vn/tasks", menu_text="Công việc", requests=[blocked, ok])
    p.modules = [ModuleInfo(name="CV", url=pg.url, pages=[pg])]
    sc = cx.build_scenario(p, UCScore(uc_code="U", uc_name="n", matched_pages=[pg.url]))
    en = {s.url: s.enabled for s in sc.steps if s.method == "POST"}
    assert en == {blocked.url: False, ok.url: True}


# ------------------------------------------------------------------ danh sách cấm & link
def test_blacklist_normalization():
    bl = [w.lower() for w in config.get("crawler.popup_blacklist", [])]
    assert is_banned("Hoàn thành", bl)                  # &nbsp;
    assert is_banned("delete_forever xem", bl)               # tên icon có '_'
    assert is_banned("Xem và duyệt", bl)     # NFD
    assert not is_banned("Xin gia hạn", bl)                  # "in" (In ấn) không khớp giữa từ


def test_content_links_excluded(tmp_path):
    from perftool.crawler.web_crawler import WebCrawler
    c = WebCrawler(LoginConfig(base_url="http://LocalHost:8189/home"), "", tmp_path)
    assert c._same_origin("http://localhost:8189/cv/list")
    assert c._same_origin("https://www.localhost/x")
    assert c._excluded("http://localhost/Account/LogOff", "Thoát")                 # đăng xuất (menu lẫn nội dung)
    assert c._excluded("http://localhost/x", "Đăng xuất")
    assert c._excluded("http://localhost/cv/approve?id=1", "Duyệt", content=True)   # GET ghi dữ liệu trong bảng
    assert c._excluded("http://localhost/cv/view?id=1", "Hoàn thành", content=True)
    assert not c._excluded("http://localhost/vif/task/viec-cho-phe-duyet", "Việc chờ phê duyệt")   # menu: vẫn mở


def test_url_helpers():
    assert page_key("http://h/cv/list") == page_key("http://H:80/cv/list/#top")
    assert page_key("http://h/d?id=1", True) == page_key("http://h/d?id=2&page=3", True)
    assert page_key("http://h/report?id=1") != page_key("http://h/report?id=2")
    assert join_url("http://h:8188/home", "/cong-viec/danh-sach") == "http://h:8188/cong-viec/danh-sach"
    assert join_url("http://h/app", "x/y") == "http://h/app/x/y"
    assert host_key("https://WWW.Sys.vn:443/a") == "sys.vn"


def test_unique_pages_collapses_content_detail_pages():
    pages = [PageInfo(url=f"https://h/news/Detail?id={i}", depth=1) for i in range(4)] + \
            [PageInfo(url="https://h/report?id=1"), PageInfo(url="https://h/report?id=2")]
    assert len(cx.unique_pages(pages)) == 3


# ------------------------------------------------------------------ bước 4
def test_compound_words_do_not_cross_match():
    thong_bao = PageInfo(url="https://h/portal/notifications", menu_text="Thông Báo", module="Thông Báo")
    danh_ba = PageInfo(url="https://h/contact", menu_text="Danh Bạ", module="Danh Bạ")
    uc = UseCase(code="U", name="Báo cáo tổng hợp dung lượng lưu trữ", module="Kho tài liệu")
    assert cx.match_pages(uc, [thong_bao, danh_ba])[0] == []
    uc2 = UseCase(code="V", name="Tìm kiếm nhanh danh bạ", module="Danh bạ")
    assert cx.match_pages(uc2, [thong_bao, danh_ba])[0] == [danh_ba]


def test_generic_titles_and_url_words():
    pages = [PageInfo(url=f"https://h/vif/task/{slug}", title="Thông tin tìm kiếm", module="Giao việc")
             for slug in ("viec-cho-phe-duyet", "viec-phoi-hop-xu-ly", "viec-can-xu-ly")]
    assert "thong tin tim kiem" in cx.generic_titles(pages)
    uc = UseCase(code="U", name="Quản lý các công việc đang chờ phê duyệt", module="Giao việc")
    got, _ = cx.match_pages(uc, pages, generic=cx.generic_titles(pages))
    assert got and got[0].url.endswith("viec-cho-phe-duyet")
    other = UseCase(code="V", name="Tìm kiếm nhanh Văn bản đến", module="Văn bản")
    assert cx.match_pages(other, pages, generic=cx.generic_titles(pages))[0] == []


@pytest.mark.parametrize("text,group,expected", [
    ("Tìm kiếm nhanh từ giao diện danh sách", "gui_chuyen", False),
    ("Tìm kiếm chương trình công tác", "gui_chuyen", False),
    ("Tra cứu theo số ký hiệu", "duyet", False),
    ("Đăng ký tài khoản", "duyet", False),
    ("Tiếp nhận đề xuất", "xuat_nhap", False),
    ("Assign address design", "them_moi", False),
    ("Them moi, sua, xoa ho so", "xoa", True),
    ("Thêm mới hồ sơ", "them_moi", True),
])
def test_operation_groups_whole_words(text, group, expected):
    kind = "data_processing" if group in ("xuat_nhap",) else "crud"
    assert (group in cx.matched_groups(text, kind)) is expected


def test_recommend_per_module_diversity_only_within_module():
    frame = ["POST h/api/checkURL_label"]
    sc = [UCScore(uc_code=f"U{i}", uc_name="x", module=f"M{i}", total=90 - i, api_paths=frame, matched_pages=[f"p{i}"])
          for i in range(5)]
    picks = [s for s in cx.recommend(sc, 3, per_module=1) if s.recommended]
    assert len(picks) == 5                 # mỗi phân hệ 1 UC dù bộ API giống nhau giữa các phân hệ
    picks = [s for s in cx.recommend(sc, 3, per_module=0) if s.recommended]
    assert len(picks) == 1                 # toàn dự án: vẫn loại UC trùng bộ API


def test_explain_ignores_zero_weight():
    w = {k: 0 for k in cx.CRITERIA}
    w["api_count"] = 1
    raw = {k: 0 for k in cx.CRITERIA}
    raw["steps"] = 8
    sc = cx.normalize(raw)
    assert "bước" not in cx.explain(raw, sc, w, 1, 1.0)


# ------------------------------------------------------------------ bước 7 & báo cáo
def test_stale_pending_scripts_detected():
    from perftool.ui.steps.s07_run import _stale
    cfg = TestConfig(vus=10)

    class R:
        config = json.loads(json.dumps(cfg.model_dump()))
    assert not _stale(R, cfg)
    cfg.vus = 2
    assert _stale(R, cfg)


def test_report_config_from_runs():
    from perftool.report.docx_report import _run_values
    cur = TestConfig(vus=2, scenario_type="smoke")
    rows = [{"scenario_type": "smoke", "config": {"vus": 5}}, {"scenario_type": "load", "config": {"vus": 9}}]
    assert _run_values(rows, cur, "vus") == "5 / 9"
    assert _run_values(rows, cur, "scenario_type", lambda v: v) == "smoke / load"
    assert _run_values([], cur, "vus") == "2"


# ------------------------------------------------------------------ lỗi mức Thấp
def test_nan_or_negative_weights_do_not_break_total():
    sc = {k: 0.5 for k in cx.CRITERIA}
    w = {k: 1.0 for k in cx.CRITERIA}
    w["steps"], w["screens"] = float("nan"), -3
    assert cx.total_score(sc, w) == 50.0
    raw = {k: 1.0 for k in cx.CRITERIA}
    raw["api_count"] = float("nan")
    assert cx.normalize(raw)["api_count"] == 0


def test_comparison_chart_many_rows(tmp_path):
    from perftool.charts.charts import comparison_chart
    rows = [{"uc_code": f"UC-{i}", "tool": "k6", "scenario_type": "load", "run_id": f"r{i}",
             "summary": {"p95": 100 + i}, "p95_threshold": 500} for i in range(12)]
    assert comparison_chart(rows, tmp_path / "c.png").exists()


def test_corrupt_record_backed_up(tmp_path):
    from perftool.crawler.manual import load_record
    f = tmp_path / "record.json"
    f.write_text('{"sessions": [{"started": "old"', encoding="utf-8")
    assert load_record(f) == {"sessions": []}
    assert list(tmp_path.glob("record.json.bak-*"))


def test_click_boundary_splits_requests():
    from perftool.crawler import manual
    rec = manual.ManualRecorder.__new__(manual.ManualRecorder)
    now = manual._ms()
    rec.seg = type("S", (), {"started_ms": now - 5000})()
    rec.session = {"events": [{"t": now - 1000, "trigger": "Xem"}, {"t": now - 500, "nav": "back_forward"}]}
    assert rec._boundary(now) == now - 1000 - manual.CLICK_LAG_MS
    rec.session = {"events": []}
    assert rec._boundary(now) == now - manual.SHIFT_MS
