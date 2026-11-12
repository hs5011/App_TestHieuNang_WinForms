"""Lưu / đọc số liệu tài nguyên máy chủ và cắt theo khung giờ từng lượt chạy."""
from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from ..storage import sub_dir
from . import METRICS

COLS = ["ts", "server", "metric", "value", "source"]
TS_FMT = "%Y-%m-%d %H:%M:%S"


def monitor_dir(project_id: str) -> Path:
    return sub_dir(project_id, "monitor")


class CsvSink:
    """Ghi nối tiếp các mẫu vào 1 file CSV (an toàn khi nhiều luồng cùng ghi)."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(",".join(COLS) + "\n", encoding="utf-8")

    def write(self, server: str, rows: Iterable[tuple[datetime, str, float]], source: str) -> int:
        n = 0
        with self.lock, open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for ts, metric, value in rows:
                w.writerow([ts.strftime(TS_FMT), server, metric, round(float(value), 4), source])
                n += 1
        return n


def load_all(project_id: str) -> pd.DataFrame:
    frames = []
    for f in sorted(monitor_dir(project_id).glob("*.csv")):
        try:
            df = pd.read_csv(f, dtype={"server": str, "metric": str, "source": str})
        except Exception:  # noqa: BLE001
            continue
        if set(COLS) <= set(df.columns) and not df.empty:
            frames.append(df[COLS])
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], format=TS_FMT, errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["ts", "value"]).drop_duplicates(subset=["ts", "server", "metric", "source"])


def run_slice(df: pd.DataFrame, started_at: str, finished_at: str) -> pd.DataFrame:
    """Mẫu nằm trong khung giờ của lượt chạy; cột t = số giây kể từ lúc bắt đầu."""
    if df.empty or not started_at or not finished_at:
        return pd.DataFrame(columns=COLS + ["t"])
    t0, t1 = pd.to_datetime(started_at), pd.to_datetime(finished_at)
    s = df[(df["ts"] >= t0) & (df["ts"] <= t1)].copy()
    s["t"] = (s["ts"] - t0).dt.total_seconds()
    return s.sort_values("ts")


def summarize(s: pd.DataFrame) -> list[dict]:
    """[{server, metric, label, unit, avg, max, n}] theo thứ tự máy chủ -> chỉ số."""
    if s.empty:
        return []
    out = []
    order = list(METRICS)
    for (server, metric), g in s.groupby(["server", "metric"], sort=False):
        label, unit = METRICS.get(metric, (metric, ""))
        out.append({"server": server, "metric": metric, "label": label, "unit": unit,
                    "avg": float(g["value"].mean()), "max": float(g["value"].max()), "n": int(len(g))})
    return sorted(out, key=lambda r: (r["server"], order.index(r["metric"]) if r["metric"] in order else 99))


def for_row(project_id: str, row: dict, df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    df = load_all(project_id) if df is None else df
    return run_slice(df, row.get("started_at", ""), row.get("finished_at", ""))


def fmt_val(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "MB/s":
        return f"{v:.2f} MB/s"
    return f"{v:.0f}"


def insights(summary: list[dict]) -> list[str]:
    """Nhận xét tự động về tài nguyên máy chủ."""
    out: list[str] = []
    for r in summary:
        name = f"{r['label']} máy chủ {r['server']}"
        if r["metric"] in ("cpu", "ram", "disk_busy"):
            if r["max"] >= 90:
                out.append(f"{name} đạt đỉnh {r['max']:.0f}% (trung bình {r['avg']:.0f}%) – tài nguyên gần cạn, "
                           "có khả năng là điểm nghẽn khi tăng tải.")
            elif r["max"] >= 75:
                out.append(f"{name} đạt đỉnh {r['max']:.0f}% (trung bình {r['avg']:.0f}%) – mức cao, cần theo dõi khi tăng tải.")
    busiest = [r for r in summary if r["metric"] == "cpu"]
    if busiest and not any(r["max"] >= 75 for r in summary if r["metric"] in ("cpu", "ram", "disk_busy")):
        b = max(busiest, key=lambda r: r["max"])
        out.append(f"Tài nguyên máy chủ còn dư: CPU cao nhất {b['max']:.0f}% ({b['server']}), chưa có dấu hiệu quá tải "
                   "phần cứng trong khung giờ kiểm thử.")
    conn = [r for r in summary if r["metric"] == "db_conn"]
    for r in conn:
        out.append(f"Số kết nối CSDL ({r['server']}) trung bình {r['avg']:.0f}, cao nhất {r['max']:.0f}.")
    return out


def import_rows(project_id: str, server: str, rows: list[tuple[datetime, str, float]], source: str, tag: str) -> Path:
    path = monitor_dir(project_id) / f"{source}_{tag}.csv"
    if path.exists():
        path.unlink()
    CsvSink(path).write(server, rows, source)
    return path


def data_files(project_id: str) -> list[Path]:
    return sorted(monitor_dir(project_id).glob("*.csv"))
