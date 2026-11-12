"""5. results: raw.csv / results.jtl rỗng hoặc bị cắt ngang, loại dòng TRANSACTION, lượt chạy toàn lỗi, chỉ lỗi 429,
đánh giá Đạt/Không đạt đúng tại ngưỡng."""
from __future__ import annotations

import pandas as pd
import pytest

from perftool.results import metrics
from perftool.results.insights import analyze, recommendations
from perftool.results.parsers import load_results, parse_jtl, parse_k6_csv

K6_HDR = ("metric_name,timestamp,metric_value,check,error,error_code,expected_response,group,method,name,proto,"
          "scenario,service,status,subproto,tls_version,url,extra_tags,metadata\n")
JTL_HDR = ("timeStamp,elapsed,label,responseCode,responseMessage,threadName,dataType,success,failureMessage,bytes,"
           "sentBytes,grpThreads,allThreads,URL,Latency,IdleTime,Connect\n")


def _k6_row(ts, v, ok=True, status=200, name="UC-1 :: A"):
    return f"http_req_duration,{ts},{v},,,,{str(ok).lower()},,GET,{name},HTTP/1.1,UC_1,,{status},,,u,uc=UC-1,\n"


def _jtl_row(ts_ms, el, label="UC-1 :: A", code="200", ok=True, threads=1):
    return f"{ts_ms},{el},{label},{code},OK,t1,text,{str(ok).lower()},,10,1,{threads},{threads},u,1,0,1\n"


# ------------------------------------------------------------------ file rỗng / chỉ tiêu đề
def test_load_results_zero_byte_and_missing(tmp_path):
    for tool, name in (("k6", "raw.csv"), ("jmeter", "results.jtl")):
        f = tmp_path / name
        f.write_bytes(b"")
        with pytest.raises(FileNotFoundError):
            load_results(tool, f)
        with pytest.raises(FileNotFoundError):
            load_results(tool, tmp_path / ("missing_" + name))


def test_k6_header_only(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text(K6_HDR)
    df, vus = parse_k6_csv(f)
    assert df.empty and vus.empty
    s = metrics.summarize(df, vus)
    assert s["samples"] == 0 and metrics.evaluate(s, 3000, 0.01)["passed"] is False
    assert metrics.per_label(df).empty and metrics.timeseries(df).empty


def test_jtl_header_only_and_wrong_format(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR)
    df, _ = parse_jtl(f)
    assert df.empty
    g = tmp_path / "x.jtl"
    g.write_text("<?xml version='1.0'?>\n<testResults/>\n")        # JTL dạng XML -> báo lỗi rõ ràng
    with pytest.raises(ValueError):
        parse_jtl(g)


# ------------------------------------------------------------------ file bị cắt ngang (tiến trình bị dừng)
def test_k6_truncated_last_line(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text(K6_HDR + _k6_row(1000, 100) + _k6_row(1001, 200) + "http_req_duration,1002,3")
    df, _ = parse_k6_csv(f)
    assert len(df) >= 2 and df["elapsed_ms"].notna().all()


def test_k6_truncated_mid_field(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text(K6_HDR + _k6_row(1000, 100) + "http_req_dur")
    df, _ = parse_k6_csv(f)
    assert len(df) == 1


def test_jtl_truncated_last_line_after_stop(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Bấm Dừng khi JMeter đang ghi -> dòng cuối JTL bị cắt ('1003000,'). _finish() bỏ dòng lỗi và sắp xếp lại,
    nhưng cột allThreads vẫn lấy theo độ dài/thứ tự cũ -> ValueError, không phân tích được lượt chạy."""
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + _jtl_row(1_000_000, 100) + _jtl_row(1_002_000, 300) + "1003000,")
    df, vus = parse_jtl(f)
    assert sorted(df["elapsed_ms"].tolist()) == [100.0, 300.0]


def test_jtl_garbage_row_in_middle(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + _jtl_row(1_000_000, 100) + "1001000,abc,UC-1 :: A\n" + _jtl_row(1_002_000, 300))
    df, _ = parse_jtl(f)
    assert sorted(df["elapsed_ms"].tolist()) == [100.0, 300.0]


def test_jtl_unsorted_rows_vus_aligned(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] JMeter ghi JTL theo thứ tự hoàn thành (timeStamp = lúc bắt đầu) nên file không theo thời gian.
    _finish() sắp xếp theo ts nhưng allThreads gán theo thứ tự gốc -> số VU bị gán lệch thời điểm."""
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + _jtl_row(1_005_000, 100, threads=50) + _jtl_row(1_000_000, 4000, threads=1))
    _, vus = parse_jtl(f)
    got = dict(zip(vus["ts"], vus["vus"]))
    assert got == {1000.0: 1, 1005.0: 50}


def test_jtl_label_with_comma_quoted(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + '1000000,100,"UC-1 :: Tìm, lọc",200,OK,t1,text,true,,10,1,1,1,u,1,0,1\n', encoding="utf-8")
    df, _ = parse_jtl(f)
    assert df["label"].tolist() == ["UC-1 :: Tìm, lọc"] and df["uc"].tolist() == ["UC-1"]


# ------------------------------------------------------------------ TRANSACTION / LOGIN
def test_transaction_rows_excluded_even_when_failed(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + _jtl_row(1_000_000, 100) + _jtl_row(1_001_000, 900, "UC-1 :: TRANSACTION", "500", False)
                 + _jtl_row(1_002_000, 900, "UC-2 :: TRANSACTION") + _jtl_row(1_003_000, 50, "UC-1 :: LOGIN"))
    df, _ = parse_jtl(f)
    assert not df["label"].str.contains("TRANSACTION").any()
    s = metrics.summarize(df[~df["is_login"]])
    assert s["samples"] == 1 and s["errors"] == 0


def test_step_named_transaction_word_not_dropped(tmp_path):
    """Chỉ bỏ nhãn kết thúc bằng ' :: TRANSACTION', không bỏ bước có chữ 'transaction' trong tên."""
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + _jtl_row(1_000_000, 100, "UC-1 :: API POST transactions"))
    assert len(parse_jtl(f)[0]) == 1


# ------------------------------------------------------------------ toàn lỗi / chỉ 429
def test_all_errors_run(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text(K6_HDR + "".join(_k6_row(1000 + i, 5, ok=False, status=0) for i in range(10)))
    df, _ = parse_k6_csv(f)
    s = metrics.summarize(df)
    assert s["errors"] == 10 and s["error_rate"] == 1.0 and s["status_codes"] == {"0": 10}
    ev = metrics.evaluate(s, 3000, 0.01)
    assert ev["passed"] is False and ev["p95_ok"] is True and ev["err_ok"] is False
    notes = analyze(s, ev, metrics.timeseries(df), metrics.per_label(df), 3000, 0.01)
    assert any("Có 10 request lỗi" in n and "100.00%" in n for n in notes)


def _row(codes, samples=1000, passed=False, err_ok=False):
    return {"uc_code": "UC-1", "tool": "k6", "scenario_type": "load",
            "summary": {"samples": samples, "errors": sum(codes.values()), "status_codes": codes,
                        "error_rate": sum(codes.values()) / samples, "max_vus": 10, "p95": 100},
            "evaluation": {"passed": passed, "p95_ok": True, "err_ok": err_ok, "reasons": []}}


def test_429_only_detection():
    rec = " ".join(recommendations([_row({"429": 200})]))
    assert "HTTP 429" in rec
    assert "Phân tích log máy chủ" not in rec           # 429 không bị coi là lỗi máy chủ thật


def test_429_mixed_with_5xx():
    rec = " ".join(recommendations([_row({"429": 200, "500": 50})]))
    assert "HTTP 429" in rec and "Phân tích log máy chủ" in rec


def test_429_detection_in_analyze_from_parsed_jtl(tmp_path):
    f = tmp_path / "r.jtl"
    f.write_text(JTL_HDR + "".join(_jtl_row(1_000_000 + i * 100, 20, code="429", ok=False) for i in range(5))
                 + _jtl_row(1_001_000, 20))
    df, _ = parse_jtl(f)
    s = metrics.summarize(df)
    ev = metrics.evaluate(s, 3000, 0.01)
    notes = analyze(s, ev, metrics.timeseries(df), metrics.per_label(df), 3000, 0.01)
    assert any("5 lỗi là HTTP 429" in n for n in notes)


# ------------------------------------------------------------------ ngưỡng: đúng bằng ngưỡng
def _df(elapsed, errors=0):
    n = len(elapsed)
    return pd.DataFrame({"ts": [1000.0 + i for i in range(n)], "label": "UC-1 :: A", "uc": "UC-1",
                         "elapsed_ms": [float(x) for x in elapsed], "success": [i >= errors for i in range(n)],
                         "status": ["500" if i < errors else "200" for i in range(n)], "bytes": 0.0,
                         "is_login": False})


def test_p95_exactly_threshold_passes():
    s = metrics.summarize(_df([3000] * 20))
    assert s["p95"] == 3000
    ev = metrics.evaluate(s, 3000, 0.01)
    assert ev["passed"] and ev["p95_ok"]
    assert not metrics.evaluate(s, 2999.99, 0.01)["p95_ok"]


def test_error_rate_exactly_threshold_passes():
    s = metrics.summarize(_df([100] * 100, errors=1))
    assert s["error_rate"] == 0.01
    assert metrics.evaluate(s, 3000, 0.01)["passed"]
    ev = metrics.evaluate(metrics.summarize(_df([100] * 100, errors=2)), 3000, 0.01)
    assert not ev["passed"] and ev["reasons"] == ["tỷ lệ lỗi 2.00% > ngưỡng 1.00%"]


def test_error_rate_threshold_float_precision():
    """3/300 = 0.01 về mặt toán học: không được trượt vì sai số dấu phẩy động."""
    s = metrics.summarize(_df([100] * 300, errors=3))
    assert metrics.evaluate(s, 3000, 0.01)["err_ok"]
    s7 = metrics.summarize(_df([100] * 700, errors=7))
    assert metrics.evaluate(s7, 3000, 0.01)["err_ok"]


def test_zero_threshold_edges():
    s = metrics.summarize(_df([100] * 10))
    assert metrics.evaluate(s, 3000, 0.0)["passed"]            # 0 lỗi, ngưỡng 0 -> đạt
    assert not metrics.evaluate(metrics.summarize(_df([100] * 10, errors=1)), 3000, 0.0)["passed"]


def test_single_sample_and_zero_duration():
    s = metrics.summarize(_df([0]))
    assert s["samples"] == 1 and s["p95"] == 0 and s["throughput"] == 1.0 and s["duration_s"] == 1.0
