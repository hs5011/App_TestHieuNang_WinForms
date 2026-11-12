"""Kiểm thử thu số liệu tài nguyên máy chủ (cấu hình ở bước 6, xem ở bước 8)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from perftool.charts import charts
from perftool.monitor import perfmon, sources, store

PDH = (
    '"(PDH-CSV 4.0)","\\\\WEB01\\Processor(_Total)\\% Processor Time","\\\\WEB01\\Memory\\Available MBytes",'
    '"\\\\WEB01\\Memory\\% Committed Bytes In Use","\\\\WEB01\\PhysicalDisk(_Total)\\% Idle Time",'
    '"\\\\WEB01\\PhysicalDisk(_Total)\\Disk Read Bytes/sec","\\\\WEB01\\PhysicalDisk(_Total)\\Disk Write Bytes/sec",'
    '"\\\\WEB01\\Network Interface(nic1)\\Bytes Total/sec","\\\\WEB01\\Network Interface(nic2)\\Bytes Total/sec"\n'
    '"09/24/2026 10:00:05.123","40.5","4096","61.2","70","1048576","2097152","524288","524288"\n'
    '"09/24/2026 10:00:10.123","90","2048","70","10","0"," ","1048576","0"\n'
    "Exiting, please wait...\n"
)


def test_pdh_parser_units_and_ram(tmp_path):
    f = tmp_path / "log.csv"
    f.write_text(PDH, encoding="utf-8")
    rows, hosts = perfmon.parse_file(f, ram_total_mb=8192)
    assert hosts == {"WEB01"}
    first = {m: v for ts, m, v in rows if ts == datetime(2026, 9, 24, 10, 0, 5, 123000)}
    assert first["cpu"] == 40.5
    assert first["ram"] == pytest.approx(50.0)            # 100 - 4096/8192
    assert first["disk_busy"] == pytest.approx(30.0)      # 100 - % Idle Time
    assert first["disk_read"] == pytest.approx(1.0) and first["disk_write"] == pytest.approx(2.0)
    assert first["net"] == pytest.approx(1.0)             # 2 card mạng cộng dồn, đổi sang MB/s
    second = {m: v for ts, m, v in rows if ts.second == 10}
    assert "disk_write" not in second                     # ô trống -> bỏ qua, không làm hỏng dòng
    # không biết tổng RAM -> dùng % Committed Bytes In Use
    rows2, _ = perfmon.parse_file(f)
    assert [v for _, m, v in rows2 if m == "ram"][0] == 61.2


def test_pdh_utf16_and_bad_file(tmp_path):
    f = tmp_path / "u16.csv"
    f.write_bytes(PDH.encode("utf-16"))
    rows, _ = perfmon.parse_file(f)
    assert len(rows) > 5
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        perfmon.parse_file(bad)


def test_readonly_guard():
    for ok in ("SELECT COUNT(*) FROM pg_stat_activity", "with x as (select 1) select * from x;",
               "SHOW STATUS LIKE 'Threads_connected'", "-- đếm\nSELECT 1"):
        sources.check_readonly(ok)
    for bad in ("DELETE FROM t", "SELECT 1; DROP TABLE t", "EXEC sp_who", "select 1 into x; update t set a=1",
                "WITH d AS (DELETE FROM t RETURNING *) SELECT count(*) FROM d"):
        with pytest.raises(ValueError):
            sources.check_readonly(bad)
    assert set(sources.DEFAULT_QUERIES) == {"sqlserver", "postgresql", "mysql", "oracle"}


def test_store_slice_summary_and_chart(tmp_path):
    sink = store.CsvSink(tmp_path / "live_x.csv")
    t = datetime(2026, 9, 24, 10, 0, 0)
    rows = [(t.replace(second=s), "cpu", v) for s, v in ((0, 20), (5, 95), (10, 40), (59, 99))]
    sink.write("Web 01", rows, "perfmon")
    sink.write("CSDL 10.0.0.9", [(t.replace(second=5), "db_conn", 42)], "db")
    df = pd.read_csv(sink.path)
    df["ts"] = pd.to_datetime(df["ts"])
    sl = store.run_slice(df, "2026-09-24 10:00:00", "2026-09-24 10:00:30")
    assert len(sl) == 4 and sl["t"].max() == 10          # mẫu 10:00:59 nằm ngoài khung giờ
    summ = store.summarize(sl)
    cpu = next(x for x in summ if x["metric"] == "cpu")
    assert cpu["max"] == 95 and cpu["avg"] == pytest.approx(155 / 3)
    notes = " ".join(store.insights(summ))
    assert "95%" in notes and "kết nối CSDL" in notes
    png = charts.resource_chart(sl, "demo", tmp_path / "res.png")
    assert png and png.stat().st_size > 5000
    assert charts.resource_chart(sl.iloc[0:0], "rỗng", tmp_path / "none.png") is None
