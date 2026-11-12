"""Import danh sách Use Case từ Excel/CSV, tự nhận diện cột."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from pathlib import Path
from typing import IO, Union

import pandas as pd
from unidecode import unidecode

from ..models import UseCase

# Các tên cột có thể gặp (đã bỏ dấu, viết thường) -> trường chuẩn
COLUMN_ALIASES: dict[str, list[str]] = {
    "code": ["ma uc", "ma use case", "ma usecase", "uc id", "id", "ma", "ma chuc nang", "uc code", "code", "stt uc"],
    "name": ["ten uc", "ten use case", "ten usecase", "use case", "usecase", "ten chuc nang", "chuc nang", "name",
             "ten", "ten giao dich"],
    "module": ["phan he", "module", "nhom chuc nang", "phan he chuc nang", "nhom", "menu", "subsystem", "phan he/module"],
    "actor": ["tac nhan", "actor", "nguoi dung", "vai tro", "doi tuong"],
    "description": ["mo ta", "description", "noi dung", "dac ta", "luong su kien", "mo ta chi tiet", "kich ban"],
    "steps": ["so buoc", "steps", "so buoc thuc hien", "buoc"],
    "frequency": ["tan suat", "frequency", "muc do su dung", "so luong su dung"],
    "complexity": ["muc do", "do phuc tap", "phan loai theo do phuc tap", "muc do phuc tap", "complexity", "bmt",
                   "phan loai"],
    "url_hint": ["url", "duong dan", "link", "man hinh"],
    "transaction": ["giao dich", "giao dich transaction", "transaction", "giao dich/transaction"],
}
FIELDS = list(COLUMN_ALIASES.keys())
FIELD_LABELS = {
    "code": "Mã UC", "name": "Tên UC", "module": "Phân hệ", "actor": "Tác nhân", "description": "Mô tả",
    "steps": "Số bước", "frequency": "Tần suất", "complexity": "Mức độ/BMT", "url_hint": "URL/màn hình gợi ý",
    "transaction": "Giao dịch (dòng con)",
}

# Dòng nhóm trong mẫu phân cấp (QĐ 671 - PL3): "A. HỆ THỐNG...", "I. Nhóm...", "1. Phân hệ..."
_GROUP_RE = re.compile(r"^\s*([A-Z]|[IVXLC]+|\d+)\.\s+\S", re.UNICODE)


def _group_level(prefix: str) -> int:
    """A/B/C… = hệ thống (0); I/II/IV… (số La Mã) = nhóm (1); 1/2/3… = phân hệ (2)."""
    if prefix.isdigit():
        return 2
    if re.fullmatch(r"[IVX]+", prefix):
        return 1
    return 0


def norm(text: object) -> str:
    s = unidecode(str(text or "")).lower().strip()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]", " ", s)).strip()


def read_table(source: Union[str, Path, IO], filename: str = "", sheet: Union[str, int, None] = 0) -> pd.DataFrame:
    """Đọc file và tự tìm dòng tiêu đề (dòng có nhiều ô khớp alias nhất trong 20 dòng đầu)."""
    name = (filename or str(source)).lower()
    if name.endswith((".csv", ".txt")):
        raw = _read_csv_ragged(source)
    else:
        raw = pd.read_excel(source, header=None, dtype=str, sheet_name=sheet)
    if raw.empty or raw.dropna(how="all").empty:
        raise ValueError("File/sheet không có dữ liệu – chọn đúng sheet chứa danh sách UC.")

    all_alias = {a for v in COLUMN_ALIASES.values() for a in v}
    best_row, best_hits = 0, -1
    for i in range(min(20, len(raw))):
        hits = sum(1 for c in raw.iloc[i].tolist() if norm(c) in all_alias)
        if hits > best_hits:
            best_row, best_hits = i, hits
    header = [str(c).strip() if pd.notna(c) else f"Cột {j+1}" for j, c in enumerate(raw.iloc[best_row].tolist())]
    # tránh trùng tên cột
    seen: dict[str, int] = {}
    for j, h in enumerate(header):
        if h in seen:
            seen[h] += 1
            header[j] = f"{h} ({seen[h]})"
        else:
            seen[h] = 0
    df = raw.iloc[best_row + 1:].copy()
    df.columns = header
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def _read_csv_ragged(source: Union[str, Path, IO]) -> pd.DataFrame:
    """Đọc CSV cho phép số cột khác nhau giữa các dòng (vd. có dòng tiêu đề phía trên)."""
    if hasattr(source, "read"):
        if hasattr(source, "seek"):
            source.seek(0)
        data = source.read()
    else:
        data = Path(source).read_bytes()
    if isinstance(data, bytes):
        # Excel "Unicode Text (.txt)" lưu UTF-16 có BOM FF FE
        encs = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1258", "latin1")
        for enc in encs:
            try:
                data = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    try:
        delim = csv.Sniffer().sniff(data[:5000], delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(data), delimiter=delim))
    width = max((len(r) for r in rows), default=0)
    rows = [[c if c != "" else None for c in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=object)


def excel_sheets(source: Union[str, Path, IO]) -> list[str]:
    try:
        return pd.ExcelFile(source).sheet_names
    except Exception:  # noqa: BLE001
        return []


def guess_mapping(columns: list[str]) -> dict[str, str]:
    """Trả về {field: tên cột} dựa trên alias; ưu tiên khớp chính xác rồi khớp chứa."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    ncols = {c: norm(c) for c in columns}
    for field, aliases in COLUMN_ALIASES.items():
        for c, n in ncols.items():
            if c not in used and n in aliases:
                mapping[field] = c
                used.add(c)
                break
    for field, aliases in COLUMN_ALIASES.items():
        if field in mapping:
            continue
        for c, n in ncols.items():
            if c not in used and any(len(a) > 3 and a in n for a in aliases):
                mapping[field] = c
                used.add(c)
                break
    return mapping


_STEP_LINE = re.compile(r"^\s*(\d+[\.\)]|bước\s*\d+|buoc\s*\d+|[-•+*])\s*", re.IGNORECASE)


def estimate_steps(description: str) -> int | None:
    if not description:
        return None
    lines = [ln for ln in re.split(r"[\r\n]+", description) if ln.strip()]
    numbered = [ln for ln in lines if _STEP_LINE.match(ln)]
    if numbered:
        return len(numbered)
    sentences = [s for s in re.split(r"[\.;]\s+", description) if len(s.strip()) > 8]
    return max(1, len(sentences)) if sentences else None


def _to_int(v: object) -> int | None:
    try:
        m = re.search(r"\d+", str(v))
        return int(m.group()) if m else None
    except Exception:  # noqa: BLE001
        return None


def to_use_cases(df: pd.DataFrame, mapping: dict[str, str], ffill_module: bool = True) -> list[UseCase]:
    work = df.copy()
    if ffill_module and mapping.get("module") in work.columns:
        work[mapping["module"]] = work[mapping["module"]].ffill()
    ucs: list[UseCase] = []
    mapped_cols = set(mapping.values())
    groups: dict[int, str] = {}          # cấp nhóm -> tên (0: hệ thống, 1: nhóm, 2: phân hệ)
    for i, row in work.iterrows():
        def val(field: str) -> str:
            c = mapping.get(field)
            if not c or c not in work.columns:
                return ""
            v = row[c]
            return "" if pd.isna(v) else unicodedata.normalize("NFC", str(v)).strip()   # NFC: chữ Việt tổ hợp -> dựng sẵn

        name = val("name")
        # xét dòng nhóm trên dữ liệu GỐC (trước ffill phân hệ – ffill làm dòng "2. Phân hệ…" có thêm ô)
        cells = [str(v).strip() for v in df.loc[i].tolist() if not pd.isna(v) and str(v).strip()]
        if not name:
            # dòng nhóm phân cấp: chỉ có 1 ô chữ dạng "A. ...", "I. ...", "1. ..."
            if len(cells) == 1 and _GROUP_RE.match(cells[0]):
                prefix = cells[0].split(".", 1)[0].strip()
                lvl = _group_level(prefix)
                groups = {k: v for k, v in groups.items() if k < lvl}
                groups[lvl] = cells[0].split(".", 1)[1].strip()
                continue
            # dòng giao dịch con của UC phía trên
            tx = val("transaction") or (val("description") if not val("code") else "")
            if tx and ucs:
                ucs[-1].extra.setdefault("transactions", []).append(tx)
            continue
        code = val("code") or f"UC-{len(ucs) + 1:03d}"
        if re.fullmatch(r"\d+", code):
            code = f"UC-{int(code):03d}"
        code = unique_code(code, {u.code for u in ucs})
        desc = val("description")
        steps = _to_int(val("steps")) if val("steps") else estimate_steps(desc)
        extra = {c: ("" if pd.isna(row[c]) else str(row[c])) for c in work.columns if c not in mapped_cols}
        if groups:
            extra["system"] = groups.get(0, "")
            extra["group"] = groups.get(1, "")
        module = val("module") or groups.get(2) or groups.get(1) or ""
        ucs.append(UseCase(code=code, name=name, module=module, actor=val("actor"), description=desc,
                           steps=steps, frequency=val("frequency"), complexity=val("complexity"),
                           url_hint=val("url_hint"), extra=extra))
    # UC có giao dịch con: số bước = số giao dịch, mô tả = danh sách giao dịch
    for u in ucs:
        txs = u.extra.get("transactions") or []
        if txs:
            if not u.steps:
                u.steps = len(txs)
            if not u.description:
                u.description = "\n".join(f"{k}. {t}" for k, t in enumerate(txs, 1))
    return ucs


def unique_code(code: str, used: set[str]) -> str:
    """Mã UC trùng (trong file hoặc trùng mã tự sinh UC-00n) -> thêm hậu tố " (2)", " (3)"…: mã UC là khoá của điểm,
    kịch bản, ngưỡng và kết quả – trùng mã làm UC sau đè UC trước."""
    if code not in used:
        return code
    k = 2
    while f"{code} ({k})" in used:
        k += 1
    return f"{code} ({k})"


def use_cases_to_df(ucs: list[UseCase]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Mã UC": u.code, "Tên UC": u.name, "Phân hệ": u.module, "Tác nhân": u.actor, "Số bước": u.steps,
        "Tần suất": u.frequency, "Mức độ/BMT": u.complexity, "URL gợi ý": u.url_hint, "Mô tả": u.description,
    } for u in ucs])


def df_to_use_cases(df: pd.DataFrame, old: list[UseCase]) -> list[UseCase]:
    """Chuyển bảng đã chỉnh sửa trên UI về danh sách UseCase (giữ extra cũ)."""
    old_map = {u.code: u for u in old}
    out = []
    for _, r in df.iterrows():
        name = str(r.get("Tên UC") or "").strip()
        if not name or name == "nan":
            continue
        name = unicodedata.normalize("NFC", name)
        code = unicodedata.normalize("NFC", str(r.get("Mã UC") or "").strip())
        if not code or code == "nan":
            code = f"UC-{len(out) + 1:03d}"
        code = unique_code(code, {u.code for u in out})
        steps = _to_int(r.get("Số bước")) if pd.notna(r.get("Số bước")) else None

        def s(k: str) -> str:
            v = r.get(k)
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else unicodedata.normalize("NFC", str(v))

        out.append(UseCase(code=code, name=name, module=s("Phân hệ"), actor=s("Tác nhân"), steps=steps,
                           frequency=s("Tần suất"), complexity=s("Mức độ/BMT"), url_hint=s("URL gợi ý"),
                           description=s("Mô tả"), extra=old_map[code].extra if code in old_map else {}))
    return out
