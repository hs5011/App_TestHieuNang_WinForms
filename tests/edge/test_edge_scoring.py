"""3. scoring / analysis: danh sách rỗng, 1 UC, điểm bằng nhau, caps auto p90 + cap_floor, operation_groups đếm loại,
common_endpoints ≥ 60%, recommend(per_module=k); request ghi mặc định tắt trong kịch bản."""
from __future__ import annotations

import pytest

from perftool.analysis import complexity as cx
from perftool.models import CapturedRequest, ModuleInfo, PageInfo, Project, UCScore, UseCase

BASE = "https://app.example.com"


def _req(path, method="GET", **kw):
    return CapturedRequest(method=method, url=BASE + path, resource_type="xhr", status=200, duration_ms=100, **kw)


# ------------------------------------------------------------------ danh sách rỗng / 1 UC
def test_score_project_empty_use_cases():
    p = Project(id="e")
    assert cx.score_project(p, caps_mode="auto") == []
    assert p.score_caps == cx.cap_floors()               # không có UC -> mức trần = mức sàn
    assert cx.recommend([], top_n=3) == [] and cx.recommend([], per_module=2) == []


def test_score_project_single_uc_without_pages():
    p = Project(id="one")
    p.use_cases = [UseCase(code="UC-1", name="Thêm mới hồ sơ", steps=4)]
    s = cx.score_project(p, caps_mode="auto")
    assert len(s) == 1 and s[0].matched_pages == [] and 0 <= s[0].total <= 100
    assert "Chưa ghép được màn hình" in s[0].reason
    picked = cx.recommend(s, top_n=5)
    assert [x.uc_code for x in picked if x.recommended] == ["UC-1"]


def test_compute_caps_single_and_all_zero():
    one = [{"steps": 40, "screens": 0, "api_count": 1, "crud": 0, "data_processing": 0, "response_time": 50}]
    caps = cx.compute_caps(one, "auto")
    floors = cx.cap_floors()
    assert caps["steps"] == 40                         # 1 UC: p90 = chính nó
    assert caps["api_count"] == floors["api_count"]    # nhỏ hơn sàn -> dùng sàn
    assert caps["screens"] == floors["screens"]        # toàn 0 -> sàn
    assert cx.compute_caps([], "auto") == {k: round(float(v), 1) for k, v in floors.items()}


def test_compute_caps_p90_index_ten_values():
    raws = [{k: 0 for k in cx.CRITERIA} | {"steps": v} for v in range(1, 11)]   # 1..10
    assert cx.compute_caps(raws, "auto")["steps"] == 9          # int(0.9*9)=8 -> giá trị thứ 9


def test_normalize_caps_zero_and_clipping():
    raw = {"steps": 100, "screens": 1, "api_count": 0, "crud": 1, "data_processing": 1, "response_time": 1}
    caps = {k: 0 for k in cx.CRITERIA}
    sc = cx.normalize(raw, caps)                        # chia cho 0 -> dùng 1, không lỗi
    assert sc["steps"] == 1.0 and sc["api_count"] == 0


def test_all_identical_scores_recommend_is_stable_and_limited():
    sc = [UCScore(uc_code=f"U{i}", uc_name="x", total=50, matched_pages=[f"p{i}"], api_paths=[f"GET a/{i}"])
          for i in range(6)]
    got = [s.uc_code for s in cx.recommend(sc, top_n=3) if s.recommended]
    assert got == ["U0", "U1", "U2"]                    # sort ổn định, giữ thứ tự gốc khi bằng điểm
    assert sum(s.selected for s in sc) == 3


def test_weights_all_zero_or_negative():
    sc = {k: 1.0 for k in cx.CRITERIA}
    assert cx.total_score(sc, {k: 0 for k in cx.CRITERIA}) == 0
    assert cx.total_score(sc, {k: -5 for k in cx.CRITERIA}) == 0
    assert sum(cx.contributions(sc, {k: 0 for k in cx.CRITERIA}).values()) == 0


# ------------------------------------------------------------------ operation_groups: đếm loại, không đếm lặp
def test_operation_groups_count_kinds_not_repetitions():
    assert cx.matched_groups("xoá xóa XOÁ delete remove", "crud") == ["xoa"]
    assert cx.matched_groups("", "crud") == [] and cx.matched_groups("", "data_processing") == []
    many = "thêm; sửa; xoá; duyệt; gửi; lưu " * 20
    assert len(cx.matched_groups(many, "crud")) == 6


def test_crud_raw_counts_kinds_and_caps_write_apis_at_two():
    uc = UseCase(code="U", name="Thêm thêm thêm sửa sửa", description="thêm mới")
    pg = PageInfo(url=BASE + "/a", crud_buttons=["Thêm", "Xoá"],
                  requests=[_req(f"/api/w{i}", "POST") for i in range(5)])
    raw = cx.compute_raw(uc, [pg], BASE)
    assert raw["crud"] == 3 + 2                         # {them_moi, sua, xoa} + tối đa 2 API ghi


# ------------------------------------------------------------------ common_endpoints ≥ 60 %
def _pages(n, shared_in):
    out = []
    for i in range(n):
        reqs = [_req(f"/api/own{i}")]
        if i < shared_in:
            reqs.append(_req("/api/shell/menu"))
        out.append(PageInfo(url=f"{BASE}/p{i}", requests=reqs))
    return out


@pytest.mark.parametrize("n,shared,excluded", [(5, 3, True), (5, 2, False), (10, 6, True), (10, 5, False),
                                               (3, 3, False)])     # < 4 trang -> không loại
def test_common_endpoints_threshold(n, shared, excluded):
    common = cx.common_endpoints(_pages(n, shared))
    assert (f"GET app.example.com/api/shell/menu" in common) is excluded
    assert not any("own" in k for k in common)


def test_common_endpoints_dedupes_pages_and_ignores_non_xhr():
    pages = _pages(4, 4) + _pages(4, 4)                  # cùng URL lặp lại -> vẫn là 4 trang
    assert len(cx.common_endpoints(pages)) == 1
    for p in pages:
        for r in p.requests:
            r.resource_type = "document"
    assert cx.common_endpoints(pages) == set()


def test_endpoint_key_normalizes_numeric_ids():
    a = cx.endpoint_key(_req("/api/tasks/123/files/9"))
    b = cx.endpoint_key(_req("/api/tasks/77/files/1"))
    assert a == b == "GET app.example.com/api/tasks/{id}/files/{id}"


# ------------------------------------------------------------------ recommend(per_module = k)
def _sc(code, mod, total, page, api):
    return UCScore(uc_code=code, uc_name=code, module=mod, total=total, matched_pages=[page], api_paths=[api])


def test_recommend_per_module_k_limits():
    scores = [_sc(f"{m}{i}", m, 100 - 10 * i - (0 if m == "A" else 1), f"p{m}{i}", f"GET {m}/{i}")
              for m in "AB" for i in range(4)]
    for k in (1, 2, 3, 10):
        picked = [s for s in cx.recommend(scores, top_n=1, per_module=k) if s.recommended]
        for m in "AB":
            assert len([s for s in picked if s.module == m]) == min(k, 4)
    # per_module bỏ qua top_n
    assert len([s for s in cx.recommend(scores, top_n=99, per_module=1) if s.recommended]) == 2


def test_recommend_empty_module_name_is_one_group():
    scores = [_sc("X1", "", 90, "p1", "GET x/1"), _sc("X2", "", 80, "p2", "GET x/2"), _sc("X3", "", 70, "p3", "GET x/3")]
    assert [s.uc_code for s in cx.recommend(scores, per_module=2) if s.recommended] == ["X1", "X2"]


def test_recommend_resets_previous_selection():
    scores = [_sc("A", "M", 90, "p1", "GET a"), _sc("B", "M", 10, "p2", "GET b")]
    for s in scores:
        s.recommended = s.selected = True
    cx.recommend(scores, top_n=1)
    assert [(s.uc_code, s.recommended, s.selected) for s in scores] == [("A", True, True), ("B", False, False)]


# ------------------------------------------------------------------ build_scenario: request ghi mặc định tắt
def _scenario_for(reqs):
    p = Project(id="sc")
    p.login.base_url = BASE
    pg = PageInfo(url=BASE + "/screen", menu_text="Màn hình", requests=reqs)
    p.modules = [ModuleInfo(name="M", url=pg.url, pages=[pg])]
    s = UCScore(uc_code="UC-1", uc_name="x", matched_pages=[pg.url])
    return {st.url.replace(BASE, ""): st.enabled for st in cx.build_scenario(p, s).steps}


def test_write_methods_disabled_by_default():
    got = _scenario_for([_req("/api/items", "GET"), _req("/api/items/1", "PUT"), _req("/api/items/1", "DELETE"),
                         _req("/api/items/1", "PATCH"), _req("/api/items/save", "POST", post_data="{}"),
                         _req("/api/items/search", "POST", post_data="{}")])
    assert got == {"/screen": True, "/api/items": True, "/api/items/1": False, "/api/items/save": False,
                   "/api/items/search": True}


@pytest.mark.parametrize("path", ["/api/budget/save", "/api/targets/create", "/api/checklist/delete",
                                  "/api/widgets/update", "/api/reports-config/remove"])
def test_write_post_not_enabled_by_substring_match(path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] build_scenario coi POST là 'truy vấn an toàn' nếu URL chứa chuỗi con get/list/report... ở BẤT KỲ đâu
    (budGET, tarGETs, checkLIST, widGETs, REPORTs-config) -> request ghi dữ liệu thật bị BẬT mặc định."""
    got = _scenario_for([_req(path, "POST", post_data='{"a":1}')])
    assert got[path] is False, f"POST {path} (ghi dữ liệu) bị bật mặc định"


def test_token_step_forced_enabled_even_if_post():
    reqs = [_req("/api/auth/token", "POST", post_data="{}", token_path="data.t", token_sig="S"),
            _req("/api/data", auth_sig="S")]
    got = _scenario_for(reqs)
    assert got["/api/auth/token"] is True
