"""Kiểm thử thuộc tính (vòng lặp ngẫu nhiên) cho bước 4 – chấm điểm & đề xuất. Tạo bởi agent kiểm thử 2026-09-30."""
import copy
import random
import unicodedata

import pytest

from perftool.analysis import complexity as cx
from perftool.models import UCScore

K = list(cx.CRITERIA)
R = random.Random(1234)


def rand_w():
    return {k: R.choice([0, 0.05, 0.1, 0.15, 0.2, 0.35, 0.5, 1.0, 3.0]) for k in K}


def test_total_equals_sum_contributions_rounding():
    worst = 0
    for _ in range(5000):
        sc = {k: round(R.random(), 3) for k in K}
        w = rand_w()
        t = cx.total_score(sc, w)
        c = sum(cx.contributions(sc, w).values())
        worst = max(worst, abs(round(c, 1) - t))
    # hiển thị "Tổng: X (đã lưu: Y)" – lệch do làm tròn từng thành phần
    assert worst <= 0.35
    print("max lệch", worst)


def test_normalize_range_and_monotonic():
    caps = cx.fixed_caps()
    for _ in range(3000):
        raw = {k: R.uniform(0, 3 * float(caps[k])) for k in K}
        n = cx.normalize(raw, caps)
        assert all(0 <= n[k] <= 1 for k in K)
        k = R.choice(K)
        raw2 = dict(raw); raw2[k] += R.uniform(0, 5)
        w = rand_w()
        assert cx.total_score(cx.normalize(raw2, caps), w) >= cx.total_score(n, w)


def test_auto_caps_p90_floored_and_floor():
    floors = cx.cap_floors()
    for _ in range(2000):
        n = R.randint(0, 40)
        raws = [{k: R.choice([0, 0, R.randint(1, 50)]) for k in K} for _ in range(n)]
        caps = cx.compute_caps(raws, "auto")
        for k in K:
            vals = sorted(r[k] for r in raws if r[k] > 0)
            exp = vals[int(0.9 * (len(vals) - 1))] if vals else 0
            assert caps[k] == round(max(float(exp), float(floors[k])), 1)
            assert caps[k] >= floors[k]


def test_operation_groups_count_types_not_repeats():
    for kind in ("crud", "data_processing"):
        for g, words in cx.operation_groups(kind).items():
            w = words[0]
            assert cx.matched_groups(f"{w} " * 50, kind).count(g) == 1


@pytest.mark.parametrize("txt", ["Thêm mới, sửa, xoá hồ sơ", "Tìm kiếm, thống kê và xuất Excel danh sách"])
def test_nfd_equals_nfc(txt):
    for kind in ("crud", "data_processing"):
        assert cx.matched_groups(unicodedata.normalize("NFD", txt), kind) == \
            cx.matched_groups(unicodedata.normalize("NFC", txt), kind)


def test_no_diacritics_detected():
    assert cx.matched_groups("Them moi, sua, xoa ho so", "crud")


@pytest.mark.parametrize("txt", ["Tìm kiếm nhanh từ giao diện danh sách", "Tìm kiếm chương trình công tác",
                                 "Tra cứu theo số ký hiệu", "Đăng ký tài khoản"])
def test_no_crud_from_compound_words(txt):
    assert cx.matched_groups(txt, "crud") == []


def _rand_scores(n, mods=4, apis=8, with_api=True):
    out = []
    for i in range(n):
        ap = sorted({f"GET h/api/{R.randint(0, apis)}" for _ in range(R.randint(0 if not with_api else 1, 4))})
        out.append(UCScore(uc_code=f"U{i:03d}", uc_name=f"uc{i}", module=f"M{R.randint(0, mods)}",
                           total=round(R.uniform(0, 100), 1), raw={"declared": R.randint(0, 3), "steps": R.randint(0, 9)},
                           matched_pages=[f"p{R.randint(0, 6)}"] if R.random() < .8 else [], api_paths=ap))
    return out


def test_recommend_invariants():
    for _ in range(1500):
        sc = _rand_scores(R.randint(0, 30), with_api=R.random() < .8)
        k = R.choice([0, 1, 2, 3, 50]); top = R.randint(1, 10)
        r = cx.recommend(copy.deepcopy(sc), top, k)
        picks = [s for s in r if s.recommended]
        if not k:
            assert len(picks) <= top
        else:
            from collections import Counter
            assert max(Counter(s.module for s in picks).values(), default=0) <= k
        for i, a in enumerate(picks):
            for b in picks[i + 1:]:
                # chống trùng API: toàn dự án khi k=0; chỉ trong CÙNG phân hệ khi đề xuất theo phân hệ
                if a.api_paths and b.api_paths and (not k or a.module == b.module):
                    assert cx._jaccard(set(a.api_paths), set(b.api_paths)) <= 0.6
                if a.matched_pages:
                    assert set(a.matched_pages) != set(b.matched_pages)
        # sắp xếp giảm dần & xác định
        assert [s.total for s in r] == sorted([s.total for s in r], reverse=True)
        sh = copy.deepcopy(sc); R.shuffle(sh)
        if len({s.total for s in sc}) == len(sc):
            assert [s.uc_code for s in cx.recommend(sh, top, k) if s.recommended] == [s.uc_code for s in picks]
        assert all(s.selected == s.recommended for s in r)
