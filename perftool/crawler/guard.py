"""Bộ chặn ghi dữ liệu: huỷ request ghi (Lưu/Xoá/Duyệt…) trước khi tới máy chủ khi crawler bấm thử
hoặc khi người dùng ghi thao tác thủ công. Request bị chặn vẫn được ghi nhận (blocked=True) để sinh kịch bản test.

Quy tắc chi tiết: xem `crawler.write_guard` trong config/settings.yaml.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional
from urllib.parse import parse_qsl, unquote, urlparse

from .. import config

LEVELS = {"keyword": "Chặn request ghi theo từ khoá (khuyến nghị)",
          "strict": "Chặn mọi request không phải GET (trừ API truy vấn)",
          "off": "Không chặn (thao tác của tôi sẽ ghi dữ liệu thật)"}

# đoạn đường dẫn là mã bản ghi (số, GUID, chuỗi hex dài) -> bỏ qua khi tìm "đoạn cuối có nghĩa"
_ID_SEG = re.compile(r"^(\d+|[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}|[0-9a-f]{16,})$", re.I)


def _ascii(s: str) -> str:
    """Giải mã %xx, bỏ dấu tiếng Việt (cập-nhật -> cap-nhat), chữ thường."""
    s = unicodedata.normalize("NFD", unquote(s or "")).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def _tokens(s: str) -> list[str]:
    """Tách từ: theo ký tự không phải chữ/số và theo chữ hoa (CheckAndApprove -> check, and, approve)."""
    s = unicodedata.normalize("NFD", unquote(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(s))


def has_keyword(text: str, words: list[str]) -> bool:
    """So khớp từ khoá trong đoạn văn bản URL (đã bỏ dấu, không phân biệt hoa thường):
      - "xoa*" (có dấu * cuối): một TỪ bắt đầu bằng xoa (xoaBanGhi, xoabanghi);
      - "=read" hoặc từ khoá ≤ 3 ký tự (gui, huy, ky…): phải là NGUYÊN một từ (tránh guid, huyen, thread);
      - từ khoá dài hơn: chứa ở bất kỳ đâu (deleteItem, cap-nhat -> capnhat).
    """
    comp, toks = _compact(text), _tokens(text)
    for w in words:
        w = str(w).strip()
        k = _compact(w)
        if not k:
            continue
        if w.endswith("*"):
            if any(t.startswith(k) for t in toks):
                return True
        elif w.startswith("=") or len(k) <= 3:        # "=read": nguyên từ (MarkRead, /read) – không khớp thread/ready
            if k in toks:
                return True
        elif k in comp:
            return True
    return False


def _last_segment(path: str) -> str:
    """Đoạn cuối CÓ NGHĨA của đường dẫn (bỏ qua mã bản ghi: /api/task/delete/5 -> delete)."""
    segs = [s for s in unquote(path).split("/") if s]
    while segs and _ID_SEG.match(segs[-1]):
        segs.pop()
    return segs[-1] if segs else ""


def _is_read_name(seg: str) -> bool:
    """Đoạn đường dẫn mang tên truy vấn: bắt đầu bằng read_prefixes (GetAll, Search…) hoặc có 1 từ khớp read_prefixes
    (CCHC_HCM_Get_ThongBao; từ ≤ 3 ký tự như get/tim phải đứng riêng)."""
    if not seg:
        return False
    pre = [_compact(w) for w in config.get("crawler.read_prefixes", []) if _compact(w)]
    comp, toks = _compact(seg), _tokens(seg)
    return (any(comp.startswith(p) for p in pre)
            or any(t == p or (len(p) > 3 and t.startswith(p)) for t in toks for p in pre))


def _write_words() -> list[str]:
    return list(config.get("crawler.write_keywords", []))


def has_write_keyword(url: str) -> bool:
    """URL mang dấu hiệu ghi dữ liệu:
      - đoạn cuối có nghĩa hoặc query string chứa từ ghi (…/delete/5, Handler.ashx?action=delete, CheckAndApprove);
      - hoặc đoạn khác của đường dẫn chứa từ ghi mà đoạn cuối KHÔNG mang tên truy vấn (/api/delete/items).
    """
    u = urlparse(url)
    words = _write_words()
    last = _last_segment(u.path)
    if _last_has_write(last, words) or _query_has_write(u.query, words):
        return True
    return has_keyword(u.path, words) and not _is_read_name(last)


# "GetForEdit", "GetUsersByRole", "LoadDataForUpdate": từ đọc đứng đầu, từ ghi chỉ là MỤC ĐÍCH sau for/by/of -> vẫn là đọc
_PURPOSE = {"for", "by", "of", "to", "before", "cho", "de"}


def _last_has_write(last: str, words: list[str]) -> bool:
    if not has_keyword(last, words):
        return False
    toks = _tokens(last)
    pre = [_compact(w) for w in config.get("crawler.read_prefixes", []) if _compact(w)]
    if toks and any(toks[0] == p or (len(p) > 3 and toks[0].startswith(p)) for p in pre):
        cut = next((i for i, t in enumerate(toks) if t in _PURPOSE), None)
        if cut is not None and not has_keyword(" ".join(toks[:cut]), words):
            return False
    return True


def _query_has_write(query: str, words: list[str]) -> bool:
    """Query string: tên tham số và giá trị ngắn xét như đoạn đường dẫn (action=delete, cmd=capnhat); giá trị dài / chứa
    đường dẫn (UrlPage=%2Fportal%2F…OneSignal) chỉ xét NGUYÊN từ để tránh khớp nhầm (Signal ≠ sign)."""
    for k, v in parse_qsl(query or "", keep_blank_values=True):
        if has_keyword(k, words):
            return True
        if "/" in v or len(v) > 60:
            if any(t in {_compact(w) for w in words} for t in _tokens(v)):
                return True
        elif has_keyword(v, words):
            return True
    return False


_FLAG_PREFIX = {"is", "has", "include", "includes", "show", "only", "with", "without", "exclude", "can", "allow"}
_ACTION_KEYS = {"action", "act", "cmd", "command", "op", "operation", "do", "method", "task", "func", "function",
                "handler", "event", "thaotac", "hanhdong"}


def _query_has_write_token(query: str, words: list[str]) -> bool:
    """Query string của GET: chỉ xét NGUYÊN từ (action=delete, markSeen=1, cmd=XoaVanBan) – tránh khớp nhầm tham số
    đọc kiểu sort=updatedDate, isDeleted=false."""
    for k, v in parse_qsl(query or "", keep_blank_values=True):
        ktoks = _tokens(k)
        if ktoks and ktoks[0] in _FLAG_PREFIX:
            continue        # cờ lọc: isCreate=false, hasDeleted=true, includeRemoved=1
        # giá trị chỉ xét với tham số hành động (action=delete, cmd=XoaVanBan); giá trị lọc trạng thái
        # (TinhTrangGiaHan=DangGiaHan, status=ChoDuyet) là đọc
        toks = ktoks + (_tokens(v) if _compact(k) in _ACTION_KEYS else [])
        for w in words:
            w = str(w).strip()
            c = _compact(w)
            if c and (any(t.startswith(c) for t in toks) if w.endswith("*") else c in toks):
                return True
    return False


# từ cuối của đoạn đường dẫn là DANH TỪ -> đoạn đó gọi tên dữ liệu, không phải hành động:
# extend-info (thông tin gia hạn), change-log, send-history, update-detail… là API đọc
_NOUN_TAIL = {"info", "infos", "information", "history", "histories", "log", "logs", "detail", "details", "list",
              "summary", "preview", "template", "templates", "config", "setting", "settings", "option", "options",
              "report", "reports", "stat", "stats", "statistic", "statistics", "count", "counts", "form", "data",
              "type", "types", "reason", "reasons", "note", "notes", "file", "files", "attachment", "attachments"}

# tham số query KHÔNG phải mã bản ghi (phân trang / lọc / sắp xếp / ngôn ngữ…)
_LIST_PARAMS = {"page", "pageindex", "pagenumber", "pageno", "size", "pagesize", "limit", "offset", "skip", "take",
                "top", "maxresultcount", "skipcount", "sort", "sorting", "sortby", "order", "orderby", "dir", "q",
                "keyword", "keywords", "search", "searchtext", "filter", "status", "type", "lang", "language",
                "culture", "from", "to", "fromdate", "todate", "date", "year", "month", "week", "day", "v", "t", "ts",
                "_", "format", "tab", "view", "mode"}
_ID_VALUE = re.compile(r"^(\d+|[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}|[0-9a-f]{16,})$", re.I)


def _noun_tail(seg: str) -> bool:
    toks = _tokens(seg)
    return len(toks) > 1 and toks[-1] in _NOUN_TAIL


# mở trang FORM (GET) không ghi gì: /VanBan/ThemMoi, /Task/Edit/5, /HoSo/CapNhat?id=1
_FORM_VERBS = {"add", "create", "new", "edit", "update", "them", "themmoi", "sua", "chinhsua", "capnhat", "modify"}


def verb_first(seg: str, words: Optional[list[str]] = None, skip: frozenset | set = frozenset()) -> bool:
    """Đoạn đường dẫn BẮT ĐẦU bằng động từ ghi: read-all, XoaVanBan, approve, MarkAsRead, delete (khác viec-can-xu-ly,
    viec-cho-phe-duyet: danh sách việc, từ ghi chỉ đứng sau; khác "XuLy" đứng một mình: tên trang danh sách)."""
    toks = _tokens(seg)
    if not toks or _noun_tail(seg):
        return False
    t0, comp = toks[0], _compact(seg)
    for w in words if words is not None else _write_words():
        w = str(w).strip()
        k = _compact(w)
        if not k or k in skip:
            continue
        if w.endswith("*") or (not w.startswith("=") and len(k) > 3):
            # động từ tiếng Việt viết liền nhiều âm tiết (CapNhatHoSo, XoaVanBan): phải có thêm tân ngữ phía sau
            if t0.startswith(k) or (len(toks) > 1 and len(k) > len(t0) and comp.startswith(k) and comp != k):
                return True
        elif t0 == k:
            return True
    return False


def record_params(query: str) -> list[str]:
    """Tham số query trỏ tới 1 bản ghi cụ thể (id=5, congViecId=668, code=ABC, guid…), bỏ tham số phân trang/lọc."""
    out = []
    for k, v in parse_qsl(query or "", keep_blank_values=True):
        kc = _compact(k)
        if kc in _LIST_PARAMS:
            continue
        if re.fullmatch(r"1\d{9}(\d{3})?", v or "") and not kc.endswith("id"):
            continue          # tham số chống cache kiểu ?d=1790321033832 (mốc thời gian epoch), không phải mã bản ghi
        if kc == "id" or kc.endswith("id") or kc.endswith("ids") or kc in ("code", "key", "uuid", "guid", "ma") \
                or _ID_VALUE.match(v or ""):
            out.append(k)
    return out


def acts_on_record(url: str) -> bool:
    """URL nhắm vào 1 bản ghi: có tham số mã bản ghi, hoặc đoạn cuối đường dẫn là mã (/5, GUID)."""
    u = urlparse(url)
    segs = [x for x in unquote(u.path).split("/") if x]
    return bool(record_params(u.query)) or bool(segs and _ID_SEG.match(segs[-1]))


_SESSION_END = {"logout", "logoff", "signout", "signoff", "dangxuat"}


def _write_signal(url: str, skip: frozenset | set = frozenset()) -> bool:
    """Dấu hiệu ghi của 1 GET (API hoặc điều hướng trang):
      - query có từ ghi (action=delete, markSeen=1);
      - một đoạn đường dẫn BẮT ĐẦU bằng động từ ghi (/inbox/read-all, /api/task/delete/5, /VanBan/XoaVanBan, …/LogOut);
      - URL trỏ tới 1 bản ghi (id=…) và đoạn cuối có từ ghi (/ho-so/xu-ly?id=1).
    Đoạn cuối mang tên truy vấn (GetList, danh-sach…) hoặc là danh từ (extend-info, change-log) -> đọc. Từ ghi đứng sau
    trong cụm danh từ tiếng Việt (gia-han/danh-sach, chiu-trach-nhiem-xu-ly, loai-xu-ly) không tính là ghi."""
    u = urlparse(url)
    words = _write_words()
    if _query_has_write_token(u.query, words):
        return True
    path = unquote(u.path)
    last = _last_segment(path)
    if _compact(last) in _SESSION_END:
        return True                       # đăng xuất: load test sẽ tự huỷ phiên của VU
    if _is_read_name(last) or _noun_tail(last):
        return False
    segs = [s for s in path.split("/") if s and not _ID_SEG.match(s)]
    if any(verb_first(s, words, skip) for s in segs):
        return True
    return acts_on_record(url) and _last_has_write(last, [w for w in words if _compact(w) not in skip])


def get_has_write(url: str) -> bool:
    """GET (xhr/fetch) mang dấu hiệu ghi dữ liệu – nhiều hệ thống cũ ghi bằng GET: /api/hoso/markread?id=1,
    /VanBan/XoaVanBan?id=5, Handler.ashx?action=delete, /history?markSeen=1. Xem _write_signal."""
    return _write_signal(url)


def classify_get(url: str) -> str:
    """Phân loại GET cho kịch bản test: "write" (có dấu hiệu ghi), "read" (chắc chắn chỉ đọc), "unknown" (chưa rõ).

    Chỉ "read" được bật sẵn ở bước 5 (whitelist). "unknown" = GET nhắm vào 1 bản ghi (id=…) mà đoạn cuối không mang tên
    truy vấn: /api/notify/ack?id=1, /api/tasks/pin?id=1 – từ ghi không thể liệt kê hết, nên để người dùng tự xác nhận.
    Danh sách / REST lấy theo mã (/api/tasks?page=1, /api/tasks/5, /danh-sach-cong-viec/370) là "read"."""
    if get_has_write(url):
        return "write"
    u = urlparse(url)
    path = unquote(u.path)
    last = _last_segment(path)
    if _is_read_name(last) or _noun_tail(last) or path.lower().endswith((".json", ".xml", ".txt", ".html")):
        return "read"
    if any(_compact(k) and _compact(k) in _compact(path) for k in config.get("crawler.read_keywords", [])):
        return "read"
    segs = [x for x in path.split("/") if x]
    if segs and _ID_SEG.match(segs[-1]):
        return "read"          # REST lấy 1 bản ghi theo mã: /api/tasks/5
    if not record_params(u.query) and not verb_first(last):
        return "read"          # danh sách / không trỏ bản ghi cụ thể
    return "unknown"


def page_has_write(url: str) -> bool:
    """Điều hướng trang (link menu / bước "Mở màn hình") có thể ghi dữ liệu: đoạn cuối bắt đầu bằng động từ ghi
    (/inbox/read-all, /VanBan/XoaVanBan), hoặc trỏ tới 1 bản ghi và có từ ghi (/ho-so/xu-ly?id=1). Trang danh sách
    "/viec-can-xu-ly", "/HoSo/XuLy" không bị coi là ghi."""
    # trang form (/VanBan/ThemMoi, /HoSo/Edit/5, /HoSo/CapNhat?id=1) chỉ hiển thị form -> đọc
    return _write_signal(url, skip=_FORM_VERBS)


def is_write_request(method: str, url: str, resource_type: str = "xhr", level: str = "keyword") -> bool:
    """True nếu request cần chặn theo mức `level` (keyword | strict | off)."""
    method = (method or "GET").upper()
    if level == "off" or method in ("HEAD", "OPTIONS"):
        return False
    if method == "GET":
        # GET do JS gọi (xhr/fetch) có từ ghi -> chặn; điều hướng trang (document) do crawler tự lọc link
        return (resource_type in ("xhr", "fetch") and bool(config.get("crawler.block_get_writes", True))
                and get_has_write(url))
    if resource_type not in ("xhr", "fetch", "document", "other", "ping", "eventsource", ""):
        return False
    if method in ("PUT", "PATCH", "DELETE"):
        return True       # luôn là ghi dữ liệu, bất kể tên đường dẫn (vd DELETE /api/timesheet)
    if has_write_keyword(url):
        return True       # mọi mức: có từ ghi ở đoạn cuối / query -> chặn (kể cả /api/tasklist/delete/5)
    if level == "strict":
        return not _is_query_name(url)
    # mức keyword: POST không rõ nghĩa được cho qua (trang cần để tải dữ liệu), trừ form POST cả trang không mang tên truy vấn
    return resource_type == "document" and not _is_query_name(url)


def _is_query_name(url: str) -> bool:
    """POST (đã biết không có từ ghi) mang tên truy vấn: đoạn cuối là tên đọc, đường dẫn chứa read_keywords,
    hoặc đoạn cuối bắt đầu bằng weak_read_prefixes (PostData, ExecuteStore…)."""
    path = urlparse(url).path
    last = _last_segment(path)
    if _compact(last) == "view":
        return False      # POST /api/hoso/view: thường là ghi nhận "đã xem" (tăng lượt xem, đánh dấu đã đọc)
    if _is_read_name(last):
        return True
    if any(_compact(k) and _compact(k) in _compact(path) for k in config.get("crawler.read_keywords", [])):
        return True
    weak = [_compact(w) for w in config.get("crawler.weak_read_prefixes", []) if _compact(w)]
    return any(_compact(last).startswith(w) for w in weak)


def classify(method: str, url: str) -> str:
    """"read" | "write" | "unknown" – chỉ "read" được bật sẵn trong kịch bản test (bước 5)."""
    method = (method or "GET").upper()
    if method in ("HEAD", "OPTIONS"):
        return "read"
    if method == "GET":
        return classify_get(url)
    if method != "POST":
        return "write"
    return "write" if is_write_request(method, url, "xhr", level="strict") else "read"


def is_query_request(method: str, url: str) -> bool:
    """True nếu request CHẮC CHẮN chỉ đọc dữ liệu, được BẬT sẵn trong kịch bản test (bước 5).

    GET: xem classify_get (GET chưa rõ nghĩa trỏ tới 1 bản ghi -> tắt). POST: không bị chặn ở mức "strict" (mang tên
    truy vấn GetAll, Search…, không có từ ghi). POST không rõ (vd /api/tasks) -> tắt mặc định để người dùng tự bật.
    """
    return classify(method, url) == "read"


class WriteGuard:
    """Gắn vào BrowserContext: huỷ request ghi, ghi lại (method, url) đã chặn."""

    def __init__(self, level: str = "keyword", log: Optional[Callable[[str], None]] = None):
        self.level = level
        self.log = log or (lambda _m: None)
        self.blocked: set[tuple[str, str]] = set()
        self.count = 0
        self.active = False
        self._ctx = None

    def attach(self, context) -> None:
        if self.level == "off" or not config.get("crawler.write_guard", True):
            return
        self._ctx = context
        context.route("**/*", self._handle)
        self.active = True

    def detach(self) -> None:
        if self._ctx and self.active:
            try:
                self._ctx.unroute("**/*", self._handle)
            except Exception:  # noqa: BLE001
                pass
        self.active = False

    def _handle(self, route) -> None:
        req = route.request
        try:
            if is_write_request(req.method, req.url, req.resource_type, self.level):
                self.blocked.add((req.method, req.url))
                self.count += 1
                self.log(f"      🛡 Đã chặn request ghi dữ liệu: {req.method} {urlparse(req.url).path}")
                route.abort("blockedbyclient")
                return
        except Exception:  # noqa: BLE001 - không xác định được -> chặn (an toàn hơn cho qua)
            try:
                route.abort("blockedbyclient")
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            route.fallback()
        except Exception:  # noqa: BLE001
            try:
                route.continue_()
            except Exception:  # noqa: BLE001
                pass
