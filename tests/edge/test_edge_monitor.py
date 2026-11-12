"""7. monitor: bộ kiểm tra câu SQL chỉ đọc (thử injection) và cắt CSV dạng long theo [started_at, finished_at]."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from perftool.models import DBMonitor
from perftool.monitor import sources, store


# ------------------------------------------------------------------ check_readonly
@pytest.mark.parametrize("sql", [
    "select 1", "   \n\tSELECT 1", "SeLeCt count(*) from t;", "WITH a AS (SELECT 1) SELECT * FROM a",
    "show status like 'Threads_connected'", "/* đếm */ SELECT 1",
    "SELECT created_at, updated_by FROM t",           # tên cột chứa 'update' nhưng không phải từ khoá riêng
])
def test_readonly_accepts(sql):
    sources.check_readonly(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE x", "SELECT 1;DROP TABLE x;", "select 1; select 2",
    "SELECT 1 -- ; DROP TABLE t",                      # thận trọng: mọi dấu ; giữa câu đều bị từ chối
    "/* x */ DELETE FROM t", "-- chỉ là ghi chú\nDELETE FROM t", "  update t set a=1",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT count(*) FROM d",
    "WITH u AS (UPDATE t SET a=1 RETURNING *) SELECT 1 FROM u",
    "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1",
    "SELECT 1 /* */; TRUNCATE t", "EXECUTE sp_x", "exec('drop table t')", "CALL p()", "MERGE INTO t USING s ON 1=1",
    "SELECT * FROM t WHERE 1=1\n;\nGRANT ALL ON t TO u", "", "   ", "-- chỉ ghi chú",
    "SHOW TABLES; ALTER TABLE t ADD c INT",
])
def test_readonly_rejects(sql):
    with pytest.raises(ValueError):
        sources.check_readonly(sql)


@pytest.mark.parametrize("sql", [
    "SELECT '--'; DROP TABLE t",
    "SELECT '/*'; DELETE FROM t; SELECT '*/'",
    "SELECT 1 AS \"--\"; DROP TABLE t",
])
def test_readonly_comment_markers_inside_string_literal(sql):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Bộ lọc xoá '--...' và '/*...*/' bằng regex mà không biết chuỗi trong nháy -> dấu '--' nằm TRONG chuỗi
    làm biến mất phần '; DROP TABLE t' khi kiểm tra, trong khi câu gốc (có DROP) vẫn được gửi tới CSDL."""
    with pytest.raises(ValueError):
        sources.check_readonly(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * INTO backup_t FROM t",                         # SQL Server / PostgreSQL: tạo bảng mới
    "SELECT * FROM t INTO OUTFILE '/tmp/x.csv'",             # MySQL: ghi file trên máy chủ CSDL
])
def test_readonly_select_into_is_a_write(sql):
    """[Hồi quy – lỗi đã sửa 2026-09-25] SELECT ... INTO tạo bảng / ghi file trên máy chủ CSDL nhưng vẫn được coi là câu 'chỉ đọc'."""
    with pytest.raises(ValueError):
        sources.check_readonly(sql)


def test_db_query_strips_trailing_semicolon_and_default():
    assert sources.db_query(DBMonitor(db_type="mysql", query="  SELECT 1 ;  ")).strip() == "SELECT 1"
    assert sources.db_query(DBMonitor(db_type="postgresql")) == sources.DEFAULT_QUERIES["postgresql"]
    for q in sources.DEFAULT_QUERIES.values():
        sources.check_readonly(q)                             # câu mặc định phải qua được bộ lọc


def test_test_db_rejects_before_connecting(monkeypatch):
    called = []
    monkeypatch.setattr(sources, "db_connect", lambda *a: called.append(a))
    with pytest.raises(ValueError):
        sources.test_db(DBMonitor(db_type="postgresql", host="127.0.0.1", query="DELETE FROM t"), "pw")
    assert called == []


def test_db_sample_non_numeric_and_empty():
    class Cur:
        def __init__(self, row):
            self.row = row

        def execute(self, sql):
            pass

        def fetchone(self):
            return self.row

        def close(self):
            pass

    class Conn:
        def __init__(self, row):
            self.row = row

        def cursor(self):
            return Cur(self.row)

    assert sources.db_sample(Conn(("Threads_connected", "17")), "SHOW") == 17.0
    with pytest.raises(ValueError):
        sources.db_sample(Conn(None), "x")
    with pytest.raises(ValueError):
        sources.db_sample(Conn(("a", "b")), "x")


# ------------------------------------------------------------------ CSV long: cắt theo khung giờ
def _df():
    t = datetime(2026, 9, 24, 10, 0, 0)
    rows = [(t.replace(second=s), "Web 01", "cpu", float(s), "perfmon") for s in (0, 1, 29, 30, 31)]
    rows.append((datetime(2026, 9, 24, 9, 59, 59), "Web 01", "cpu", 99.0, "perfmon"))
    return pd.DataFrame(rows, columns=store.COLS)


def test_run_slice_inclusive_boundaries():
    s = store.run_slice(_df(), "2026-09-24 10:00:00", "2026-09-24 10:00:30")
    assert s["value"].tolist() == [0.0, 1.0, 29.0, 30.0]      # gồm cả 2 mốc biên, bỏ 09:59:59 và 10:00:31
    assert s["t"].tolist() == [0.0, 1.0, 29.0, 30.0]


def test_run_slice_empty_inputs_and_reversed_window():
    df = _df()
    for a, b in (("", "2026-09-24 10:00:30"), ("2026-09-24 10:00:00", ""), ("", "")):
        out = store.run_slice(df, a, b)
        assert out.empty and "t" in out.columns
    assert store.run_slice(df, "2026-09-24 10:00:30", "2026-09-24 10:00:00").empty
    assert store.run_slice(df.iloc[0:0], "2026-09-24 10:00:00", "2026-09-24 10:00:30").empty


def test_run_slice_single_instant_window():
    s = store.run_slice(_df(), "2026-09-24 10:00:29", "2026-09-24 10:00:29")
    assert s["value"].tolist() == [29.0]


def test_load_all_skips_corrupt_and_foreign_csv_and_dedupes(tmp_path):
    pid = "mon-edge"
    d = store.monitor_dir(pid)
    sink = store.CsvSink(d / "live_a.csv")
    t = datetime(2026, 9, 24, 10, 0, 0)
    sink.write("Web 01", [(t, "cpu", 10), (t, "cpu", 10)], "perfmon")     # trùng -> gộp
    (d / "rac.csv").write_text("a,b\n1,2\n", encoding="utf-8")            # không đúng cột -> bỏ qua
    (d / "hong.csv").write_bytes(b"\x00\xff\xfe garbage")
    (d / "bad_ts.csv").write_text("ts,server,metric,value,source\nabc,W,cpu,1,perfmon\n"
                                  "2026-09-24 10:00:05,W,cpu,x,perfmon\n", encoding="utf-8")
    df = store.load_all(pid)
    assert len(df) == 1 and df.iloc[0]["value"] == 10
    assert str(d).startswith(str(tmp_path))


def test_summarize_and_insights_empty():
    assert store.summarize(pd.DataFrame(columns=store.COLS)) == []
    assert store.insights([]) == []
