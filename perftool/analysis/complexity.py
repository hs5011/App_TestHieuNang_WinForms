"""Ghép UC với màn hình thực tế và chấm điểm độ phức tạp để đề xuất UC kiểm thử hiệu năng."""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from typing import Optional
from urllib.parse import urlparse

from rapidfuzz import fuzz

from .. import config
from ..crawler.guard import is_query_request, page_has_write
from ..crawler.interactions import screen_key
from ..models import CapturedRequest, PageInfo, Project, ScenarioStep, TestScenario, UCScore, UseCase
from ..uc_import.importer import estimate_steps, norm

CRITERIA = {
    "steps": "Số bước",
    "screens": "Số màn hình",
    "api_count": "Số API/request",
    "crud": "Thao tác CRUD",
    "data_processing": "Xử lý dữ liệu",
    "response_time": "Thời gian phản hồi",
}
CRITERIA_UNIT = {"steps": "bước", "screens": "màn hình", "api_count": "API", "crud": "điểm", "data_processing": "điểm",
                 "response_time": "ms"}
TRACKER_HOSTS = ("google-analytics", "googletagmanager", "doubleclick", "facebook", "hotjar", "clarity.ms",
                 "sentry", "newrelic", "gstatic", "fonts.googleapis")


def default_weights() -> dict[str, float]:
    w = config.get("scoring.weights", {}) or {}
    return {k: float(w.get(k, 0.1)) for k in CRITERIA}


# ------------------------------------------------------------------ matching
def _page_text(p: PageInfo) -> str:
    return norm(f"{p.menu_text} {p.title}")


# Từ phổ biến trong tên UC/menu tiếng Việt, không mang nghĩa phân biệt màn hình (đã bỏ dấu)
VI_STOPWORDS = {
    "xem", "danh", "sach", "quan", "ly", "cua", "va", "theo", "tu", "cac", "nguoi", "dung", "he", "thong", "tren",
    "nen", "tang", "web", "cho", "mot", "nhung", "trong", "duoc", "co", "the", "khi", "voi", "chi", "tiet", "lap",
    "moi", "nhanh", "dien", "phan", "don", "vi", "noi", "bo", "tp", "hcm", "thanh", "pho", "so", "hien", "thi",
    "thuc", "hien", "tren", "nay", "do", "la", "o", "tai", "ve", "de", "sau", "truoc", "tat", "ca",
}
# Từ ghép mà từng tiếng riêng lẻ mang nghĩa khác hẳn -> gộp thành 1 từ khoá, không giữ tiếng lẻ ("bao" của thông báo
# ≠ báo cáo ≠ cảnh báo; "tri" của quản trị). Từ ghép khác (công việc, giao việc) vẫn tách tiếng để "viec" khớp nhau.
SPLIT_OFF = {"thong bao", "bao cao", "canh bao", "quan tri", "thong tin", "tim kiem", "tra cuu", "giao dien", "danh ba",
             "tin tuc", "ho so", "chia se", "lien he", "binh luan", "chuyen muc", "khao sat", "phe duyet", "thu vien",
             "chuong trinh", "quy trinh", "de xuat", "ky hieu", "dang ky", "trinh duyet", "dang nhap", "thong ke",
             "tong hop", "du lieu", "trang chu", "cap nhat", "nguoi dung", "he thong", "chi tiet", "danh sach"}
# Từ ghép / từ chỉ THAO TÁC hoặc chung chung: không dùng để ghép UC với màn hình (vd "Tìm kiếm nhanh văn bản" không được
# ghép vào trang "Thông tin tìm kiếm" của hệ Giao việc chỉ vì chung chữ "tìm kiếm")
GENERIC_KW = {
    "danh_sach", "he_thong", "nguoi_dung", "thong_tin", "chi_tiet", "cap_nhat", "tim_kiem", "tra_cuu", "quan_tri",
    "dang_nhap", "xu_ly", "theo_doi", "tong_hop", "du_lieu", "trang_chu", "tim", "kiem", "tra", "cuu", "loc", "them",
    "sua", "xoa", "tao", "cap", "nhat", "xuat", "nhap", "tri", "tin", "search", "list", "view", "detail", "edit", "add",
    "new", "index", "home", "main", "app", "portal", "page", "default",
}


def keywords(text: str) -> set[str]:
    """Từ khoá phân biệt màn hình: từ ghép 2 tiếng trong COMPOUNDS (nối bằng '_') + từ đơn không thuộc từ dừng."""
    toks = norm(text).replace("/", " ").split()
    out: set[str] = set()
    i = 0
    while i < len(toks):
        pair = f"{toks[i]} {toks[i + 1]}" if i + 1 < len(toks) else ""
        if pair in SPLIT_OFF:               # "thong bao" -> 1 từ "thong_bao" (không để lại "bao" trùng với "bao cao")
            out.add(pair.replace(" ", "_"))
            i += 2
            continue
        if len(toks[i]) > 1 and toks[i] not in VI_STOPWORDS:
            out.add(toks[i])
        i += 1
    return out - GENERIC_KW


def _kw_score(a: set[str], b: set[str]) -> float:
    """Điểm 0..100 theo từ khoá chung (có xét cặp từ ghép như 'giao viec', 'tin tuc')."""
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return 100 * len(inter) / min(len(a), len(b)) * 0.6 + 100 * len(inter) / max(len(a), len(b)) * 0.4


_ID_SEG = re.compile(r"^(\d+|[0-9a-f-]{16,})$", re.I)


def url_words(url: str) -> str:
    """Chữ trong 2 đoạn cuối đường dẫn (viec-cho-phe-duyet, CBCC_TinTuc_Detail -> 'viec cho phe duyet', 'cbcc tin tuc detail')."""
    segs = [x for x in urlparse(url).path.split("/") if x and not _ID_SEG.match(x)][-2:]
    txt = " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", x) for x in segs)
    return re.sub(r"[-_.]+", " ", txt)


def _title_text(p: PageInfo, generic_titles: Optional[set[str]] = None) -> str:
    t = norm(p.title)
    return "" if generic_titles and t in generic_titles else p.title


def page_keywords(p: PageInfo, drop: Optional[set[str]] = None, generic_titles: Optional[set[str]] = None) -> set[str]:
    return keywords(f"{p.menu_text} {_title_text(p, generic_titles)} {p.module}") - (drop or set())


def generic_titles(pages: list[PageInfo], min_pages: int = 3) -> set[str]:
    """Tiêu đề lặp lại trên nhiều màn hình ("Thông tin tìm kiếm", "CHÍNH QUYỀN SỐ") -> không mô tả màn hình cụ thể."""
    cnt: Counter[str] = Counter(norm(p.title) for p in unique_pages(pages) if norm(p.title))
    return {t for t, c in cnt.items() if c >= min_pages}


def common_page_words(pages: list[PageInfo], ratio: float = 0.5, min_pages: int = 3) -> set[str]:
    """Từ xuất hiện trên đa số màn hình (tên thương hiệu trong tiêu đề, lời chào...) -> không phân biệt được màn hình.
    Không tính tên phân hệ (phân hệ nhiều màn hình không bị mất tên) và phải xuất hiện ở ≥ 2 phân hệ."""
    pages = unique_pages(pages)
    if len(pages) < min_pages:
        return set()
    df: Counter[str] = Counter()
    mods: dict[str, set[str]] = {}
    for p in pages:
        ws = keywords(f"{p.menu_text} {p.title}")
        df.update(ws)
        for w in ws:
            mods.setdefault(w, set()).add(p.module)
    n_mod = len({p.module for p in pages})
    return {w for w, c in df.items() if c / len(pages) >= ratio and (n_mod < 2 or len(mods[w]) >= 2)}


def match_pages(uc: UseCase, pages: list[PageInfo], max_pages: int = 3,
                drop: Optional[set[str]] = None, generic: Optional[set[str]] = None) -> tuple[list[PageInfo], float]:
    if not pages:
        return [], 0.0
    uc_kw = keywords(uc.name) - (drop or set())
    mod_kw = keywords(uc.module) - (drop or set())
    tx_kw = keywords(" ".join(uc.extra.get("transactions", [])[:10])) if isinstance(uc.extra.get("transactions"), list) else set()
    hint = uc.url_hint.strip().lower()
    scored: list[tuple[float, PageInfo]] = []
    for p in pages:
        if hint and hint in p.url.lower():
            scored.append((100.0, p))
            continue
        page_kw = page_keywords(p, drop, generic)
        # chữ trên URL (viec-cho-phe-duyet) chỉ được CỘNG khi khớp với UC – không làm loãng điểm của trang
        page_kw = page_kw | ((keywords(url_words(p.url)) - (drop or set())) & (uc_kw | mod_kw))
        s_name = _kw_score(uc_kw, page_kw)
        s_mod = _kw_score(mod_kw, page_kw)
        s_tx = _kw_score(tx_kw, page_kw) * 0.6
        # cần ít nhất 1 từ khoá chung (đã bỏ từ thao tác / chung chung) với tên UC hoặc phân hệ; fuzzy chỉ bổ trợ
        if not s_name and not s_mod:
            scored.append((0.0, p))
            continue
        fz = fuzz.token_set_ratio(" ".join(sorted(uc_kw)), " ".join(sorted(page_kw)))
        scored.append((0.45 * s_name + 0.25 * s_mod + 0.1 * s_tx + 0.2 * fz, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][0]
    if best < 40:
        return [], best / 100
    chosen = [p for s, p in scored if s >= max(40, best - 8)][:max_pages] or [scored[0][1]]
    return chosen, min(best, 100) / 100


# ------------------------------------------------------------------ requests
def _base_domain(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


NOISE_PATH = re.compile(r"(negotiate|signalr|sockjs|heartbeat|/ping$|\.json$)", re.IGNORECASE)


def endpoint_key(r: CapturedRequest) -> str:
    u = urlparse(r.url)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", u.path)
    return f"{r.method} {u.hostname}{path}"


def unique_pages(pages: list[PageInfo]) -> list[PageInfo]:
    """Khử trùng màn hình theo screen_key (/list = /list/ = /list#top; detail?id=1, id=2… mở từ nội dung = 1 màn hình)."""
    seen: dict[str, PageInfo] = {}
    for p in pages:
        seen.setdefault(screen_key(p), p)
    return list(seen.values())


def common_endpoints(pages: list[PageInfo], ratio: float = 0.6, min_pages: int = 4) -> set[str]:
    """API khung (gọi trên hầu hết mọi trang: session, cấu hình, menu, thông báo...) -> không phản ánh độ phức tạp UC."""
    pages = unique_pages(pages)
    if len(pages) < min_pages:
        return set()
    cnt: Counter[str] = Counter()
    for p in pages:
        cnt.update({endpoint_key(r) for r in p.requests if r.resource_type in ("xhr", "fetch")})
    return {k for k, c in cnt.items() if c / len(pages) >= ratio}


def relevant_requests(pages: list[PageInfo], base_url: str, exclude: Optional[set[str]] = None) -> list[CapturedRequest]:
    base = _base_domain(urlparse(base_url).hostname or "")
    exclude = exclude or set()
    out = []
    for p in pages:
        for r in p.requests:
            u = urlparse(r.url)
            host = u.hostname or ""
            if any(t in host for t in TRACKER_HOSTS):
                continue
            if base and _base_domain(host) != base:
                continue
            if r.resource_type in ("xhr", "fetch") and (NOISE_PATH.search(u.path) or endpoint_key(r) in exclude):
                continue
            out.append(r)
    return out


# ------------------------------------------------------------------ scoring
def _kw_hits(text: str, words: list[str]) -> int:
    t = f" {text.lower()} "
    return sum(1 for w in words if w.lower() in t)


DEFAULT_GROUPS = {
    "crud": {"them_moi": ["thêm", "tạo", "add", "create"], "sua": ["sửa", "cập nhật", "edit", "update"],
             "xoa": ["xoá", "xóa", "delete", "remove"], "duyet": ["duyệt", "phê duyệt", "ký", "approve"],
             "gui_chuyen": ["gửi", "trình", "chuyển", "phân công", "giao", "submit", "assign"],
             "luu_tai_len": ["lưu", "tải lên", "đính kèm", "save", "upload"]},
    "data_processing": {"tim_kiem": ["tìm kiếm", "tra cứu", "lọc", "search", "filter"],
                        "thong_ke": ["thống kê", "báo cáo", "tổng hợp", "biểu đồ", "dashboard", "report"],
                        "xuat_nhap": ["xuất", "import", "export", "excel", "pdf"],
                        "danh_sach": ["danh sách", "phân trang", "sắp xếp", "list"],
                        "theo_doi": ["theo dõi", "giám sát", "cảnh báo", "monitor"]},
}
GROUP_LABELS = {
    "them_moi": "thêm mới", "sua": "sửa", "xoa": "xoá", "duyet": "duyệt/ký", "gui_chuyen": "gửi/chuyển/giao",
    "luu_tai_len": "lưu/tải lên", "tim_kiem": "tìm kiếm/lọc", "thong_ke": "thống kê/báo cáo", "xuat_nhap": "xuất/nhập file",
    "danh_sach": "danh sách/phân trang", "theo_doi": "theo dõi/cảnh báo",
}


def operation_groups(kind: str) -> dict[str, list[str]]:
    return config.get(f"scoring.operation_groups.{kind}") or DEFAULT_GROUPS[kind]


# Cụm từ chứa từ khoá thao tác nhưng KHÔNG phải thao tác đó (đã bỏ dấu): "giao diện" ≠ giao việc, "đề xuất" ≠ xuất file
NOT_OPERATION = ["giao dien", "chuong trinh", "quy trinh", "trinh duyet", "trinh do", "de xuat", "ky hieu", "dang ky",
                 "ky thuat", "ky nang", "thoi ky", "chu ky", "giao thong", "giao tiep", "giao ban",
                 "trinh bay", "xuat xu", "xuat hien", "xuat sac"]


def matched_groups(text: str, kind: str) -> list[str]:
    """Các LOẠI thao tác (nhóm) xuất hiện trong văn bản – mỗi nhóm tính 1 lần dù từ khoá lặp nhiều lần.
    So khớp NGUYÊN TỪ trên văn bản đã bỏ dấu (Unicode dựng sẵn / tổ hợp / không dấu cho cùng kết quả; "address" không chứa
    "add"), sau khi bỏ các cụm từ trong NOT_OPERATION."""
    t = f" {norm(text)} "
    for ph in NOT_OPERATION:
        t = t.replace(f" {ph} ", " # ")
    t = t.replace("  ", " ")
    return [g for g, words in operation_groups(kind).items()
            if any(norm(w) and re.search(rf"(?<![a-z0-9]){re.escape(norm(w))}(?![a-z0-9])", t) for w in words)]


def compute_raw(uc: UseCase, pages: list[PageInfo], base_url: str,
                common: Optional[set[str]] = None) -> dict[str, float]:
    text = f"{uc.name} {uc.description}"
    reqs = relevant_requests(pages, base_url, common)
    apis = [r for r in reqs if r.resource_type in ("xhr", "fetch")]
    endpoints = {endpoint_key(r) for r in apis}
    writes = {endpoint_key(r) for r in apis if r.method in ("POST", "PUT", "PATCH", "DELETE")}
    crud_btns = {b for p in pages for b in p.crud_buttons}
    durations = [r.duration_ms for r in reqs if r.duration_ms]
    if durations:
        durations.sort()
        rt = durations[min(len(durations) - 1, int(len(durations) * 0.9))]   # ~p90
    else:
        loads = [p.load_time_ms for p in pages if p.load_time_ms]
        rt = statistics.mean(loads) if loads else 0.0
    tables = sum(p.tables for p in pages)
    rows = sum(p.table_rows for p in pages)
    big_resp = sum(1 for r in reqs if (r.response_size or 0) > 100_000)

    steps = uc.steps if uc.steps else (estimate_steps(uc.description) or 0)
    # CRUD: số loại thao tác ghi (từ tên/mô tả UC + nhãn nút CRUD trên màn hình) + tối đa 2 điểm cho API ghi dữ liệu
    crud_groups = set(matched_groups(text, "crud")) | set(matched_groups(" ".join(crud_btns), "crud"))
    # Xử lý dữ liệu: số loại thao tác xử lý + có bảng dữ liệu + bảng lớn (≥50 dòng) + có phản hồi lớn (>100 KB)
    data_groups = set(matched_groups(text, "data_processing"))
    return {
        "steps": float(steps),
        "screens": float(len(pages) + sum(1 for p in pages for pp in p.popups if pp.counted)),
        "api_count": float(len(endpoints) if endpoints else len(reqs)),
        "crud": float(len(crud_groups) + min(len(writes), 2)),
        "data_processing": float(len(data_groups) + (1 if tables else 0) + (1 if rows >= 50 else 0)
                                 + (1 if big_resp else 0)),
        "response_time": round(float(rt), 1),
    }


def _cfg_caps(key: str, defaults: dict[str, float]) -> dict[str, float]:
    c = config.get(key, {}) or {}
    return {"steps": c.get("steps", defaults["steps"]), "screens": c.get("screens", defaults["screens"]),
            "api_count": c.get("api_count", defaults["api_count"]), "crud": c.get("crud", defaults["crud"]),
            "data_processing": c.get("data_processing", defaults["data_processing"]),
            "response_time": c.get("response_time_ms", defaults["response_time"])}


def fixed_caps() -> dict[str, float]:
    return _cfg_caps("scoring.caps", {"steps": 15, "screens": 5, "api_count": 20, "crud": 6, "data_processing": 6,
                                      "response_time": 2000})


def cap_floors() -> dict[str, float]:
    return _cfg_caps("scoring.cap_floor", {"steps": 3, "screens": 2, "api_count": 3, "crud": 2, "data_processing": 2,
                                           "response_time": 300})


def compute_caps(raws: list[dict[str, float]], mode: str = "auto") -> dict[str, float]:
    """Mức trần cho từng tiêu chí.
    auto : p90 của các giá trị > 0 trong dự án (không nhỏ hơn mức sàn) -> tiêu chí nào cũng phân biệt được UC;
    fixed: giá trị cố định trong config/settings.yaml."""
    if mode != "auto":
        return fixed_caps()
    floors, out = cap_floors(), {}
    for k in CRITERIA:
        vals = sorted(r.get(k, 0) for r in raws if r.get(k, 0) > 0)
        p90 = vals[int(0.9 * (len(vals) - 1))] if vals else 0   # làm tròn xuống: ít UC thì bỏ qua giá trị ngoại lai lớn nhất
        out[k] = round(max(float(p90), float(floors[k])), 1)
    return out


def _pos(v) -> float:
    """Số không âm hữu hạn (NaN / âm / sai kiểu -> 0) – settings.yaml hoặc project.json sửa tay không làm hỏng tổng điểm."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def normalize(raw: dict[str, float], caps: Optional[dict[str, float]] = None) -> dict[str, float]:
    caps = caps or fixed_caps()
    return {k: round(min(_pos(raw.get(k, 0)) / (_pos(caps.get(k)) or 1), 1.0), 3) for k in CRITERIA}


def contributions(scores: dict[str, float], weights: dict[str, float]) -> dict[str, float]:
    """Số điểm (trên thang 100) mà từng tiêu chí góp vào tổng điểm."""
    wsum = sum(_pos(weights.get(k, 0)) for k in CRITERIA) or 1
    return {k: round(100 * _pos(scores.get(k, 0)) * _pos(weights.get(k, 0)) / wsum, 1) for k in CRITERIA}


def total_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    wsum = sum(_pos(weights.get(k, 0)) for k in CRITERIA) or 1
    return round(100 * sum(_pos(scores.get(k, 0)) * _pos(weights.get(k, 0)) for k in CRITERIA) / wsum, 1)


def explain(raw: dict[str, float], scores: dict[str, float], weights: dict[str, float], matched: int,
            confidence: float) -> str:
    contrib = sorted(CRITERIA, key=lambda k: _pos(scores.get(k, 0)) * _pos(weights.get(k, 0)), reverse=True)
    parts = []
    for k in contrib[:3]:
        if _pos(scores.get(k, 0)) <= 0 or _pos(weights.get(k, 0)) <= 0:     # tiêu chí trọng số 0 không góp điểm -> không "nổi bật"
            continue
        v = raw.get(k, 0)
        v_txt = f"{v:.0f}" if k != "response_time" else f"{v:.0f}"
        parts.append(f"{CRITERIA[k].lower()} {v_txt} {CRITERIA_UNIT[k]}")
    txt = "Nổi bật: " + ", ".join(parts) if parts else "Ít dấu hiệu phức tạp"
    if matched == 0:
        txt += " · Chưa ghép được màn hình thực tế (chỉ dựa trên mô tả UC)"
    elif confidence < 0.65:
        txt += f" · Độ tin cậy ghép màn hình thấp ({confidence:.0%}), nên kiểm tra lại"
    return txt


def score_project(project: Project, weights: Optional[dict[str, float]] = None,
                  caps_mode: Optional[str] = None) -> list[UCScore]:
    """Chấm điểm toàn bộ UC. Mức trần đã dùng được ghi vào project.score_caps."""
    weights = weights or project.weights or default_weights()
    mode = caps_mode or project.caps_mode or config.get("scoring.caps_mode", "auto")
    pages = unique_pages(project.all_pages())
    common = common_endpoints(pages)
    drop = common_page_words(pages)
    gen = generic_titles(pages)
    old = {s.uc_code: s for s in project.scores}
    prepared = []
    for uc in project.use_cases:
        matched, conf = match_pages(uc, pages, drop=drop, generic=gen)
        # giữ ghép thủ công của người dùng nếu đã chỉnh
        prev = old.get(uc.code)
        if prev and prev.note.startswith("manual-match"):
            matched = [p for p in pages if p.url in prev.matched_pages]
            conf = 1.0
        raw = compute_raw(uc, matched, project.login.base_url, common)
        raw["declared"] = float(_declared_rank(uc.complexity))
        prepared.append((uc, matched, conf, raw, prev))
    caps = compute_caps([r for _, _, _, r, _ in prepared], mode)
    project.score_caps = caps
    out: list[UCScore] = []
    for uc, matched, conf, raw, prev in prepared:
        sc = normalize(raw, caps)
        api_paths = sorted({endpoint_key(r) for r in relevant_requests(matched, project.login.base_url, common)
                            if r.resource_type in ("xhr", "fetch")})
        out.append(UCScore(uc_code=uc.code, uc_name=uc.name, module=uc.module, raw=raw, scores=sc,
                           total=total_score(sc, weights), matched_pages=[p.url for p in matched],
                           api_paths=api_paths,
                           match_confidence=round(conf, 2), reason=explain(raw, sc, weights, len(matched), conf),
                           note=prev.note if prev else ""))
    return out


def _declared_rank(text: str) -> int:
    t = norm(text)
    if "phuc tap" in t or t in ("c", "cao", "high", "complex"):
        return 3
    if "trung binh" in t or t in ("b", "medium"):
        return 2
    if "don gian" in t or t in ("a", "low", "simple"):
        return 1
    return 0


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def recommend(scores: list[UCScore], top_n: int = 3, per_module: int = 0,
              diversity: float = 0.6) -> list[UCScore]:
    """Đề xuất UC.
    per_module = 0 : lấy top_n UC điểm cao nhất toàn dự án;
    per_module = k : mỗi phân hệ lấy tối đa k UC điểm cao nhất (bỏ qua top_n).
    Luôn áp dụng: không chọn 2 UC cùng bộ màn hình / bộ API trùng > `diversity`, và (khi có dữ liệu crawl) bỏ UC không có API riêng."""
    per_module = max(int(per_module), 0) if per_module else 0      # tương thích cũ: True -> 1
    ranked = sorted(scores, key=lambda s: (s.total, s.raw.get("declared", 0), s.raw.get("steps", 0)), reverse=True)
    for s in ranked:
        s.recommended = False
    picks: list[UCScore] = []
    has_api_data = any(s.api_paths for s in ranked)
    used_page_sets: set[frozenset] = set()
    seen_mod: dict[str, int] = {}
    for s in ranked:
        key = frozenset(s.matched_pages)
        # tránh chọn nhiều UC cùng trỏ về đúng một bộ màn hình (sẽ sinh script trùng nhau)
        if s.matched_pages and key in used_page_sets:
            continue
        # khi đã có dữ liệu crawl: chỉ đề xuất UC có API riêng (không có API -> script chỉ mở trang tĩnh)
        if has_api_data and not s.api_paths:
            continue
        # tránh chọn UC dùng gần như cùng bộ API (trùng > diversity) với UC đã chọn (vd. cùng 1 framework lưới dữ liệu);
        # khi đề xuất theo phân hệ chỉ so với UC đã chọn CÙNG phân hệ (phân hệ khác là chức năng khác)
        mine = set(s.api_paths)
        if mine and any(_jaccard(mine, set(o.api_paths)) > diversity for o in picks
                        if o.api_paths and (not per_module or o.module == s.module)):
            continue
        if per_module and seen_mod.get(s.module, 0) >= per_module:
            continue
        picks.append(s)
        used_page_sets.add(key)
        seen_mod[s.module] = seen_mod.get(s.module, 0) + 1
        if not per_module and len(picks) >= top_n:
            break
    for s in picks:
        s.recommended = True
        s.selected = True
    for s in ranked:
        if not s.recommended:
            s.selected = False
    return ranked


# ------------------------------------------------------------------ scenario
def build_scenario(project: Project, score: UCScore) -> TestScenario:
    all_pages = unique_pages(project.all_pages())
    common = common_endpoints(all_pages)
    pages = [p for p in all_pages if p.url in score.matched_pages]
    steps: list[ScenarioStep] = []
    seen: set[str] = set()
    think = project.test_config.think_time_s
    winforms = getattr(project, "kind", "web") == "winforms"
    for p in pages:
        if p.url not in seen and not winforms:     # WinForms: màn hình là URL ảo, không có trang để GET – chỉ phát lại API
            # trang mà mở ra là ghi dữ liệu (/inbox/read-all, /ho-so/xu-ly?id=1) -> tắt mặc định
            steps.append(ScenarioStep(name=f"Mở màn hình {p.menu_text or p.title or ''}".strip()[:80],
                                      method="GET", url=p.url, think_time_s=0, enabled=not page_has_write(p.url)))
            seen.add(p.url)
        for r in relevant_requests([p], project.login.base_url, common):
            if r.resource_type not in ("xhr", "fetch"):
                continue
            key = f"{r.method} {r.url}"
            if key in seen:
                continue
            seen.add(key)
            path = urlparse(r.url).path.rstrip("/").split("/")[-1] or urlparse(r.url).path
            # chỉ request CHẮC CHẮN là đọc mới bật (whitelist): GET danh sách/chi tiết, POST mang tên truy vấn. Ghi dữ
            # liệu, chưa rõ nghĩa (GET /api/notify/ack?id=1), chứa dữ liệu nhạy cảm, hoặc đã bị bộ chặn ghi huỷ -> tắt
            steps.append(ScenarioStep(name=f"API {r.method} {path}"[:80], method=r.method, url=r.url,
                                      body=r.post_data or "", content_type=_ctype_from(r),
                                      enabled=is_query_request(r.method, r.url) and not r.blocked and not r.sensitive,
                                      headers=dict(r.headers)))
        if steps:
            steps[-1].think_time_s = think
    _link_tokens(steps, [r for pg in pages for r in pg.requests])
    if not steps and project.login.base_url:
        steps.append(ScenarioStep(name="Trang chủ", url=project.login.base_url, think_time_s=think))
    return TestScenario(uc_code=score.uc_code, uc_name=score.uc_name, module=score.module, steps=steps)


def _link_tokens(steps: list[ScenarioStep], reqs: list[CapturedRequest]) -> None:
    """Nếu 1 request cấp token (vd GetToken) và các request sau dùng đúng token đó -> trích & gắn tự động."""
    by_key = {f"{r.method} {r.url}": r for r in reqs}
    sig_to_var: dict[str, str] = {}
    for st in steps:
        r = by_key.get(f"{st.method} {st.url}")
        if not r:
            continue
        if r.auth_sig and r.auth_sig in sig_to_var:
            st.use_token = sig_to_var[r.auth_sig]
        if r.token_path and r.token_sig:
            var = f"TOKEN_{len(sig_to_var) + 1}"
            st.extract_token, st.token_var = r.token_path, var
            sig_to_var[r.token_sig] = var
            st.enabled = True   # bước lấy token là bắt buộc cho các bước sau


def _ctype_from(r: CapturedRequest) -> str:
    if not r.post_data:
        return ""
    body = r.post_data.strip()
    if body.startswith(("{", "[")):
        return "application/json"
    if "=" in body:
        return "application/x-www-form-urlencoded"
    return "text/plain"
