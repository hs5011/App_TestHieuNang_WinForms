"""1. uc_import: Excel/CSV lỗi định dạng, thiếu cột, sheet rỗng, ô gộp, dấu tiếng Việt, mã trùng, mô tả dài, mã hoá CSV."""
from __future__ import annotations

import io
import unicodedata

import openpyxl
import pandas as pd
import pytest

from perftool.uc_import import importer


def _xlsx(tmp_path, rows, merges=(), name="uc.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    f = tmp_path / name
    wb.save(f)
    return f


def _import(src, filename):
    df = importer.read_table(src, filename)
    m = importer.guess_mapping(list(df.columns))
    return df, m, importer.to_use_cases(df, m)


# ------------------------------------------------------------------ thiếu cột
def test_only_name_column_autogenerates_codes(tmp_path):
    f = _xlsx(tmp_path, [["Tên UC"], ["Xem danh sách"], ["Thêm mới"]])
    _, m, ucs = _import(f, f.name)
    assert m == {"name": "Tên UC"}
    assert [u.code for u in ucs] == ["UC-001", "UC-002"]
    assert all(u.module == "" and u.steps is None for u in ucs)


def test_missing_name_column_yields_no_use_cases(tmp_path):
    f = _xlsx(tmp_path, [["Mã UC", "Ghi chú"], ["UC-1", "x"], ["UC-2", "y"]])
    df, m, ucs = _import(f, f.name)
    assert "name" not in m
    assert ucs == []                                   # không có tên -> không tạo UC rác


def test_missing_mapped_column_in_df_is_ignored():
    df = pd.DataFrame({"Tên UC": ["A"]})
    ucs = importer.to_use_cases(df, {"name": "Tên UC", "module": "Không tồn tại", "steps": "Cũng không"})
    assert len(ucs) == 1 and ucs[0].module == "" and ucs[0].steps is None


# ------------------------------------------------------------------ sheet / file rỗng
def test_empty_excel_sheet_raises_cleanly(tmp_path):
    """File rỗng: UI bắt ngoại lệ (s02_import) -> chỉ yêu cầu không treo / không trả dữ liệu rác."""
    f = _xlsx(tmp_path, [])
    with pytest.raises(Exception):
        importer.read_table(f, f.name)


def test_empty_csv_raises_cleanly():
    with pytest.raises(Exception):
        importer.read_table(io.BytesIO(b""), "a.csv")


def test_header_only_csv_gives_empty_list():
    df, m, ucs = _import(io.BytesIO("Mã UC,Tên UC\n".encode()), "a.csv")
    assert len(df) == 0 and ucs == []


def test_whitespace_only_rows_are_dropped():
    csv = "Mã UC,Tên UC,Phân hệ\n,,\nUC-1,A,M\n,,\n"
    _, _, ucs = _import(io.BytesIO(csv.encode()), "a.csv")
    assert [u.code for u in ucs] == ["UC-1"]


# ------------------------------------------------------------------ ô gộp (merged cells)
def test_merged_module_cells_are_forward_filled(tmp_path):
    rows = [["Mã UC", "Tên UC", "Phân hệ"],
            ["UC-1", "Xem công việc", "Quản lý công việc"],
            ["UC-2", "Thêm công việc", None],
            ["UC-3", "Xoá công việc", None],
            ["UC-4", "Tra cứu danh bạ", "Danh bạ"]]
    f = _xlsx(tmp_path, rows, merges=["C2:C4"])
    _, _, ucs = _import(f, f.name)
    assert [u.module for u in ucs] == ["Quản lý công việc"] * 3 + ["Danh bạ"]
    # tắt ffill -> ô gộp phía dưới trống
    df = importer.read_table(f, f.name)
    ucs2 = importer.to_use_cases(df, importer.guess_mapping(list(df.columns)), ffill_module=False)
    assert ucs2[1].module == ""


def test_merged_title_row_above_header(tmp_path):
    rows = [["DANH SÁCH USE CASE", None, None], [None, None, None],
            ["Mã UC", "Tên UC", "Mô tả"], ["UC-1", "A", "1. Mở\n2. Xem\n3. Đóng"]]
    f = _xlsx(tmp_path, rows, merges=["A1:C1"])
    _, m, ucs = _import(f, f.name)
    assert m["code"] == "Mã UC" and ucs[0].steps == 3


# ------------------------------------------------------------------ dấu tiếng Việt
def test_vietnamese_header_nfd_normalization():
    """Tiêu đề ở dạng tổ hợp (NFD - macOS / một số bộ gõ) vẫn nhận diện đúng."""
    hdr = [unicodedata.normalize("NFD", h) for h in ("Mã UC", "Tên chức năng", "Phân hệ", "Mô tả")]
    csv = ",".join(hdr) + "\nUC-1,Thống kê,Báo cáo,x\n"
    _, m, ucs = _import(io.BytesIO(csv.encode("utf-8")), "a.csv")
    assert set(m) >= {"code", "name", "module", "description"}
    assert ucs[0].name == "Thống kê"


def test_vietnamese_text_preserved(tmp_path):
    f = _xlsx(tmp_path, [["Mã UC", "Tên UC", "Tác nhân"], ["UC-Đ1", "Đăng ký hồ sơ ưu đãi – “đặc biệt”", "Lãnh đạo"]])
    _, _, ucs = _import(f, f.name)
    assert ucs[0].code == "UC-Đ1" and ucs[0].name == "Đăng ký hồ sơ ưu đãi – “đặc biệt”" and ucs[0].actor == "Lãnh đạo"


# ------------------------------------------------------------------ mã trùng
def test_duplicate_column_names_are_disambiguated(tmp_path):
    f = _xlsx(tmp_path, [["Mã UC", "Tên UC", "Ghi chú", "Ghi chú"], ["UC-1", "A", "x", "y"]])
    df = importer.read_table(f, f.name)
    assert list(df.columns) == ["Mã UC", "Tên UC", "Ghi chú", "Ghi chú (1)"]


def test_numeric_codes_are_zero_padded():
    csv = "Mã UC,Tên UC\n7,A\n12,B\n"
    _, _, ucs = _import(io.BytesIO(csv.encode()), "a.csv")
    assert [u.code for u in ucs] == ["UC-007", "UC-012"]


def test_duplicate_codes_are_kept_not_silently_dropped():
    """Mã trùng trong file gốc: giữ cả 2 UC (không mất dữ liệu) và khử trùng mã – mã UC là khoá của điểm/kịch bản/
    ngưỡng/kết quả, trùng mã làm UC sau đè UC trước và bước 5 lỗi khoá trùng."""
    csv = "Mã UC,Tên UC\nUC-1,A\nUC-1,B\n,C\nUC-003,D\n"
    _, _, ucs = _import(io.BytesIO(csv.encode()), "a.csv")
    assert [(u.code, u.name) for u in ucs] == [("UC-1", "A"), ("UC-1 (2)", "B"), ("UC-003", "C"), ("UC-003 (2)", "D")]


# ------------------------------------------------------------------ mô tả rất dài / số bước
def test_very_long_description():
    desc = "\n".join(f"{i}. Bước thao tác số {i} với nội dung rất dài " + "x" * 500 for i in range(1, 201))
    df = pd.DataFrame({"Mã UC": ["UC-1"], "Tên UC": ["A"], "Mô tả": [desc]})
    ucs = importer.to_use_cases(df, importer.guess_mapping(list(df.columns)))
    assert ucs[0].steps == 200 and len(ucs[0].description) == len(desc)


def test_steps_column_non_numeric_and_estimate_edges():
    df = pd.DataFrame({"Mã UC": ["UC-1", "UC-2", "UC-3"], "Tên UC": ["A", "B", "C"],
                       "Số bước": ["khoảng 5 bước", "không rõ", None], "Mô tả": ["", "", "Mở. Xem"]})
    ucs = importer.to_use_cases(df, importer.guess_mapping(list(df.columns)))
    assert ucs[0].steps == 5
    assert ucs[1].steps is None                            # không có số -> None, không crash
    assert importer.estimate_steps("") is None and importer.estimate_steps("ok") is None


# ------------------------------------------------------------------ CSV encodings / dấu phân cách
@pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig"])
def test_csv_utf8_variants(enc):
    csv = "Mã UC;Tên UC;Phân hệ\nUC-1;Thống kê;Báo cáo\n"
    _, m, ucs = _import(io.BytesIO(csv.encode(enc)), "a.csv")
    assert m["code"] == "Mã UC" and ucs[0].name == "Thống kê" and ucs[0].module == "Báo cáo"


def _cp1258(text: str) -> bytes:
    """Mã hoá như bộ gõ Windows: nguyên âm có mũ/móc dựng sẵn + dấu thanh tổ hợp."""
    out = b""
    for ch in unicodedata.normalize("NFC", text):
        try:
            out += ch.encode("cp1258")
        except UnicodeEncodeError:
            d = unicodedata.normalize("NFD", ch)
            shape = "̛̂̆"            # mũ, trăng, móc -> dựng sẵn; dấu thanh -> tổ hợp
            base = unicodedata.normalize("NFC", d[0] + "".join(c for c in d[1:] if c in shape))
            rest = "".join(c for c in d[1:] if c not in shape)
            out += (base + rest).encode("cp1258")
    return out


def test_csv_cp1258_windows_vietnamese():
    data = _cp1258("Mã UC,Tên UC,Phân hệ\nUC-1,Thống kê,Báo cáo\n")
    _, m, ucs = _import(io.BytesIO(data), "a.csv")
    assert m.get("code") and m.get("name") and m.get("module")
    assert unicodedata.normalize("NFC", ucs[0].name) == "Thống kê"


def test_csv_tab_and_pipe_delimiters():
    for sep in ("\t", "|"):
        csv = sep.join(["Mã UC", "Tên UC"]) + "\n" + sep.join(["UC-1", "A, B"]) + "\n"
        _, _, ucs = _import(io.BytesIO(csv.encode()), "a.txt")
        assert ucs[0].name == "A, B", sep


def test_csv_utf16_excel_unicode_text():
    """Excel 'Lưu thành Unicode Text (.txt)' xuất UTF-16 LE + tab. File .txt được chấp nhận ở bước 2."""
    text = "Mã UC\tTên UC\tPhân hệ\r\nUC-1\tThống kê\tBáo cáo\r\n"
    _, m, ucs = _import(io.BytesIO(text.encode("utf-16")), "uc.txt")
    assert m.get("name") == "Tên UC", f"Không nhận diện được tiêu đề của file UTF-16: {m}"
    assert ucs and ucs[0].name == "Thống kê"


def test_csv_ragged_rows_and_quoted_newlines():
    csv = 'Danh sách\nMã UC,Tên UC,Mô tả\nUC-1,A,"1. a\n2. b"\nUC-2,B\n'
    _, _, ucs = _import(io.BytesIO(csv.encode()), "a.csv")
    assert [u.code for u in ucs] == ["UC-1", "UC-2"] and ucs[0].steps == 2


def test_df_to_use_cases_roundtrip_and_blank_rows():
    ucs = [importer.UseCase(code="UC-1", name="A", extra={"k": 1})]
    df = importer.use_cases_to_df(ucs)
    df.loc[len(df)] = {"Mã UC": None, "Tên UC": None}          # dòng trống từ data_editor
    df.loc[len(df)] = {"Mã UC": "", "Tên UC": "Mới", "Số bước": float("nan")}
    out = importer.df_to_use_cases(df, ucs)
    assert [u.code for u in out] == ["UC-1", "UC-002"] and out[0].extra == {"k": 1} and out[1].steps is None
