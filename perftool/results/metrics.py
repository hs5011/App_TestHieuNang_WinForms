"""Tính các chỉ số hiệu năng từ DataFrame chuẩn."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

METRIC_LABELS = {
    "samples": "Tổng số request", "errors": "Số request lỗi", "error_rate": "Tỷ lệ lỗi",
    "throughput": "Thông lượng (req/s)", "avg": "Thời gian phản hồi trung bình", "min": "Nhỏ nhất",
    "p50": "Phân vị 50 (p50)", "p90": "Phân vị 90 (p90)", "p95": "Phân vị 95 (p95)", "p99": "Phân vị 99 (p99)",
    "max": "Thời gian phản hồi lớn nhất", "max_vus": "Số người dùng đồng thời tối đa", "duration_s": "Thời lượng (s)",
}


def summarize(df: pd.DataFrame, vus: Optional[pd.DataFrame] = None) -> dict:
    n = int(len(df))
    if n == 0:
        return {"samples": 0, "errors": 0, "error_rate": 0.0, "throughput": 0.0, "avg": None, "min": None,
                "p50": None, "p90": None, "p95": None, "p99": None, "max": None, "max_vus": 0, "duration_s": 0,
                "avg_vus": 0, "status_codes": {}}
    e = df["elapsed_ms"].to_numpy(dtype=float)
    errors = int((~df["success"]).sum())
    start, end = float(df["ts"].min()), float((df["ts"] + df["elapsed_ms"] / 1000).max())
    dur = max(end - start, 1.0)
    q = np.percentile(e, [50, 90, 95, 99])
    res = {
        "samples": n, "errors": errors, "error_rate": errors / n, "throughput": n / dur,
        "avg": float(e.mean()), "min": float(e.min()), "p50": float(q[0]), "p90": float(q[1]),
        "p95": float(q[2]), "p99": float(q[3]), "max": float(e.max()), "duration_s": round(dur, 1),
        "start_ts": start, "end_ts": end,
        "status_codes": df.loc[~df["success"], "status"].value_counts().head(10).to_dict(),
    }
    if vus is not None and len(vus):
        v = vus[(vus["ts"] >= start - 1) & (vus["ts"] <= end + 1)]["vus"]
        res["max_vus"] = int(v.max()) if len(v) else 0
        res["avg_vus"] = float(v.mean()) if len(v) else 0
    else:
        res["max_vus"], res["avg_vus"] = 0, 0
    return res


def per_label(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    rows = []
    for label, g in df.groupby("label", sort=False):
        s = summarize(g)
        rows.append({"label": label, "uc": g["uc"].iloc[0], "samples": s["samples"], "errors": s["errors"],
                     "error_rate": s["error_rate"], "throughput": s["throughput"], "avg": s["avg"], "min": s["min"],
                     "p50": s["p50"], "p90": s["p90"], "p95": s["p95"], "p99": s["p99"], "max": s["max"]})
    return pd.DataFrame(rows).sort_values("p95", ascending=False).reset_index(drop=True)


def per_uc(df: pd.DataFrame, vus: Optional[pd.DataFrame] = None, include_login: bool = False) -> dict[str, dict]:
    work = df if include_login else df[~df["is_login"]]
    return {uc: summarize(g, vus) for uc, g in work.groupby("uc", sort=False)}


def timeseries(df: pd.DataFrame, vus: Optional[pd.DataFrame] = None, max_points: int = 120) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["t", "rps", "avg", "p95", "error_rate", "vus"])
    t0 = float(df["ts"].min())
    total = max(float(df["ts"].max()) - t0, 1)
    bucket = max(1, math.ceil(total / max_points))
    b = ((df["ts"] - t0) // bucket).astype(int)
    g = df.groupby(b)
    ts = pd.DataFrame({
        "t": g.size().index * bucket,
        "rps": g.size().values / bucket,
        "avg": g["elapsed_ms"].mean().values,
        "p95": g["elapsed_ms"].quantile(0.95).values,
        "error_rate": (1 - g["success"].mean()).values,
        "errors": (~df["success"]).groupby(b).sum().values,
    })
    if vus is not None and len(vus):
        vb = ((vus["ts"] - t0) // bucket).astype(int)
        vv = vus.groupby(vb)["vus"].max()
        ts["vus"] = ts["t"].div(bucket).astype(int).map(vv).ffill().fillna(0).values
    else:
        ts["vus"] = np.nan
    return ts


def evaluate(summary: dict, p95_threshold: float, err_threshold: float) -> dict:
    """So sánh với ngưỡng đánh giá -> kết luận Đạt / Không đạt kèm lý do."""
    reasons = []
    p95 = summary.get("p95")
    er = summary.get("error_rate") or 0.0
    p95_ok = p95 is not None and p95 <= p95_threshold
    er_ok = er <= err_threshold
    if summary.get("samples", 0) == 0:
        return {"passed": False, "p95_ok": False, "err_ok": False, "reasons": ["Không có dữ liệu"]}
    if not p95_ok:
        reasons.append(f"p95 {p95:.0f} ms > ngưỡng {p95_threshold:.0f} ms")
    if not er_ok:
        reasons.append(f"tỷ lệ lỗi {er * 100:.2f}% > ngưỡng {err_threshold * 100:.2f}%")
    return {"passed": p95_ok and er_ok, "p95_ok": p95_ok, "err_ok": er_ok, "reasons": reasons}


def fmt_ms(v: Optional[float]) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.0f} ms"


def fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.2f}%"


def fmt_num(v: Optional[float], nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"
