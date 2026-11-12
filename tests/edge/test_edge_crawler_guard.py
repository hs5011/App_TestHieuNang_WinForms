"""6. crawler guard.py (route giả lập), popup_blacklist so khớp nguyên từ, merge.apply_record() idempotent."""
from __future__ import annotations

import json
import unicodedata
from types import SimpleNamespace

import pytest

from perftool import config
from perftool.crawler import merge
from perftool.crawler.guard import WriteGuard, is_write_request
from perftool.crawler.interactions import is_banned
from perftool.models import CapturedRequest, ModuleInfo, PageInfo, Project


class FakeRoute:
    def __init__(self, method, url, rtype="xhr", fallback_fails=False):
        self.request = SimpleNamespace(method=method, url=url, resource_type=rtype)
        self.calls = []
        self._ff = fallback_fails

    def abort(self, reason=""):
        self.calls.append(("abort", reason))

    def fallback(self):
        if self._ff:
            raise RuntimeError("no fallback in this playwright")
        self.calls.append(("fallback",))

    def continue_(self):
        self.calls.append(("continue",))


class FakeContext:
    def __init__(self):
        self.routes, self.unrouted = [], []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def unroute(self, pattern, handler):
        self.unrouted.append(pattern)


# ------------------------------------------------------------------ WriteGuard với route giả lập
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "delete"])
def test_guard_blocks_write_methods(method):
    g = WriteGuard()
    r = FakeRoute(method, "https://x.vn/api/tasks/save/1")
    g._handle(r)
    assert r.calls == [("abort", "blockedbyclient")] and g.count == 1
    assert (method, "https://x.vn/api/tasks/save/1") in g.blocked


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_guard_allows_head_options_even_with_write_words(method):
    g = WriteGuard()
    r = FakeRoute(method, "https://x.vn/api/tasks/delete?id=1")
    g._handle(r)
    assert r.calls == [("fallback",)] and g.count == 0


@pytest.mark.parametrize("method", ["GET", "get", None])
def test_guard_blocks_get_xhr_with_write_words(method):
    """Nhiều hệ thống cũ ghi dữ liệu bằng GET -> GET xhr/fetch có từ ghi cũng bị chặn khi quét."""
    g = WriteGuard()
    r = FakeRoute(method, "https://x.vn/api/tasks/delete?id=1")
    g._handle(r)
    assert r.calls == [("abort", "blockedbyclient")] and g.count == 1


def test_guard_get_document_navigation_not_blocked():
    """Điều hướng trang (document) do crawler tự lọc link, bộ chặn không huỷ."""
    g = WriteGuard()
    r = FakeRoute("GET", "https://x.vn/HoSo/XuLy", rtype="document")
    g._handle(r)
    assert r.calls == [("fallback",)]


def test_guard_fail_closed_on_error(monkeypatch):
    import perftool.crawler.guard as gm
    monkeypatch.setattr(gm, "is_write_request", lambda *a, **k: 1 / 0)
    r = FakeRoute("POST", "https://x.vn/api/x")
    WriteGuard()._handle(r)
    assert r.calls == [("abort", "blockedbyclient")]


def test_guard_falls_back_to_continue_on_old_playwright():
    r = FakeRoute("GET", "https://x.vn/", fallback_fails=True)
    WriteGuard()._handle(r)
    assert r.calls == [("continue",)]


def test_guard_static_resources_pass_through():
    for rtype in ("image", "stylesheet", "script", "font", "media"):
        r = FakeRoute("POST", "https://x.vn/upload/delete.png", rtype)
        WriteGuard()._handle(r)
        assert r.calls == [("fallback",)], rtype


def test_guard_level_off_and_attach_detach(monkeypatch):
    ctx = FakeContext()
    g = WriteGuard(level="off")
    g.attach(ctx)
    assert ctx.routes == [] and not g.active
    g2 = WriteGuard()
    g2.attach(ctx)
    assert ctx.routes and g2.active
    g2.detach()
    assert ctx.unrouted == ["**/*"] and not g2.active
    r = FakeRoute("DELETE", "https://x.vn/api/a/1")
    g._handle(r)                                          # level off: không chặn
    assert r.calls == [("fallback",)]


def test_guard_logs_path_not_query_secrets():
    logs = []
    g = WriteGuard(log=logs.append)
    g._handle(FakeRoute("POST", "https://x.vn/api/user/save?token=SECRET123"))
    assert logs and "SECRET123" not in logs[0]


@pytest.mark.parametrize("method,url", [
    ("DELETE", "https://x.vn/api/timesheet"),            # 'tim' (tìm) là read_prefix
    ("PUT", "https://x.vn/api/countries"),               # 'count'
    ("PUT", "https://x.vn/api/user/layout"),             # 'lay' (lấy)
    ("PATCH", "https://x.vn/api/documents/view-state"),  # 'view'
    ("DELETE", "https://x.vn/api/tasks/getting-started"),  # 'get'
])
def test_put_patch_delete_always_blocked_at_keyword_level(method, url):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Mức keyword: tài liệu/ghi chú nói 'PUT/PATCH/DELETE luôn là ghi dữ liệu', nhưng kiểm tra read_prefixes
    (tiền tố của đoạn cuối URL) chạy TRƯỚC kiểm tra phương thức -> DELETE /api/timesheet, PUT /api/countries... lọt qua."""
    assert is_write_request(method, url), f"{method} {url} không bị chặn"


def test_read_prefix_allows_post_query():
    assert not is_write_request("POST", "https://x.vn/api/Task/GetAllPaged")
    assert not is_write_request("POST", "https://x.vn/api/tasks/search")


# ------------------------------------------------------------------ popup_blacklist: so khớp nguyên từ
BL = config.get("crawler.popup_blacklist", [])


@pytest.mark.parametrize("label,banned", [
    ("Xoá", True), ("XÓA", True), ("Xóa lọc", True),      # nguyên từ 'xoá' xuất hiện -> chặn (bảo thủ, đúng thiết kế)
    ("Xoáy", False), ("Tải về", True), ("In", True), ("Xin ý kiến", False), ("Print", True),
    ("Xem chi tiết", False), ("Thêm mới", False), ("Sửa", False), ("Ký số", True), ("Kỹ thuật", False),
    ("  Lưu  ", True), ("Lưu trữ", True), ("Deleted items", False), ("delete", True), ("", False),
])
def test_popup_blacklist_whole_word(label, banned):
    assert is_banned(label, BL) is banned


def test_blacklist_entries_with_spaces_and_empty():
    assert not is_banned("abc", ["", "   "])
    assert is_banned("đánh dấu đã đọc", ["đánh dấu"])
    assert not is_banned("đánh dấuX", ["đánh dấu"])


def test_blacklist_decomposed_unicode_label():
    """[Hồi quy – lỗi đã sửa 2026-09-25] Trang dùng 'Unicode tổ hợp' (dấu thanh là ký tự riêng - vẫn phổ biến ở web cơ quan nhà nước):
    nhãn 'Xóa' dạng NFD không khớp mục 'xóa' (NFC) trong popup_blacklist -> crawler bấm thử nút Xoá."""
    label = unicodedata.normalize("NFD", "Xóa")
    assert is_banned(label, BL)


# ------------------------------------------------------------------ merge.apply_record
def _req(url, method="GET", **kw):
    return CapturedRequest(method=method, url=url, resource_type="xhr", **kw)


def _proj():
    p = Project(id="rec-edge")
    p.login.base_url = "https://a.vn"
    pg = PageInfo(url="https://a.vn/app/list", menu_text="DS", requests=[_req("https://a.vn/api/list")])
    p.modules = [ModuleInfo(name="M", url=pg.url, pages=[pg])]
    return p


def _write(p, data):
    merge.record_file(p).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _snapshot(p):
    return json.dumps(p.model_dump(exclude={"updated_at"}), sort_keys=True, ensure_ascii=False)


def test_apply_record_no_file_and_corrupted_file():
    p = _proj()
    before = _snapshot(p)
    assert merge.apply_record(p) == 0 and _snapshot(p) == before
    _write(p, "{not json")
    assert merge.apply_record(p) == 0 and _snapshot(p) == before
    _write(p, {"sessions": [{"segments": [{"kind": "page"}]}]})       # đoạn không có URL -> bỏ qua
    assert merge.apply_record(p) == 1 and _snapshot(p) == before


def test_apply_record_idempotent_three_times_with_duplicate_segments():
    p = _proj()
    seg = {"kind": "page", "url": "https://a.vn/app/list", "page_url": "https://a.vn/app/list", "title": "DS",
           "requests": [_req("https://a.vn/api/list/count").model_dump()]}
    pop = {"kind": "popup", "url": "https://a.vn/app/list", "title": "Chi tiết", "stats": {"inputs": 2},
           "requests": [_req("https://a.vn/api/detail").model_dump()]}
    _write(p, {"sessions": [{"started": "s1", "segments": [seg, pop, seg]},
                            {"started": "s2", "segments": [pop, seg]}]})
    merge.apply_record(p)
    first = _snapshot(p)
    merge.apply_record(p)
    merge.apply_record(p)
    assert _snapshot(p) == first
    page = p.modules[0].pages[0]
    assert [r.url for r in page.requests] == ["https://a.vn/api/list", "https://a.vn/api/list/count",
                                               "https://a.vn/api/detail"]
    assert len([pp for pp in page.popups if pp.origin == "manual"]) == 1


def test_apply_record_does_not_touch_crawled_data_after_clear():
    p = _proj()
    base = _snapshot(p)
    _write(p, {"sessions": [{"segments": [{"kind": "page", "url": "https://zz.vn/x", "title": "Ngoài",
                                           "requests": [_req("https://zz.vn/api/x").model_dump()]}]}]})
    merge.apply_record(p)
    assert any(m.name == merge.MANUAL_MODULE for m in p.modules)
    merge.clear_record(p)
    assert _snapshot(p) == base
