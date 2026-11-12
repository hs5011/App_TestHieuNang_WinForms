"""Đọc kết quả thô của k6 (CSV output) và JMeter (JTL CSV) về một định dạng chung.

DataFrame chuẩn gồm các cột:
  ts (float, giây epoch) | label | uc | elapsed_ms | success (bool) | status (str) | bytes
và DataFrame vus: ts | vus
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEP = " :: "
STD_COLS = ["ts", "label", "uc", "elapsed_ms", "success", "status", "bytes", "is_login"]


def _uc_of(label: pd.Series) -> pd.Series:
    return label.str.split(SEP, n=1, regex=False).str[0]


def parse_k6_csv(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    usecols = ["metric_name", "timestamp", "metric_value", "expected_response", "name", "status", "scenario"]
    chunks, vus_chunks, lost_chunks = [], [], []
    for ch in pd.read_csv(path, usecols=lambda c: c in usecols, dtype=str, chunksize=500_000):
        m = ch["metric_name"]
        d = ch[m == "http_req_duration"]
        if len(d):
            chunks.append(d)
        lo = ch[m == "perftool_session_lost"]
        if len(lo):
            lost_chunks.append(lo[["timestamp", "name"]])
        v = ch[m == "vus"]
        if len(v):
            vus_chunks.append(v[["timestamp", "metric_value"]])
    if chunks:
        d = pd.concat(chunks, ignore_index=True)
        out = pd.DataFrame({
            "ts": pd.to_numeric(d["timestamp"], errors="coerce").astype(float),
            "label": d["name"].fillna("").astype(str),
            "elapsed_ms": pd.to_numeric(d["metric_value"], errors="coerce").astype(float),
            "success": d["expected_response"].astype(str).str.lower().eq("true"),
            "status": d["status"].fillna("").astype(str),
            "bytes": np.nan,
        })
        if lost_chunks:
            _mark_session_lost(out, pd.concat(lost_chunks, ignore_index=True))
    else:
        out = pd.DataFrame(columns=STD_COLS)
    out = _finish(out)
    if vus_chunks:
        v = pd.concat(vus_chunks, ignore_index=True)
        vus = pd.DataFrame({"ts": pd.to_numeric(v["timestamp"], errors="coerce").astype(float),
                            "vus": pd.to_numeric(v["metric_value"], errors="coerce")})
        vus = vus.groupby("ts", as_index=False)["vus"].max()
    else:
        vus = pd.DataFrame(columns=["ts", "vus"])
    return out, vus


def _mark_session_lost(out: pd.DataFrame, lost: pd.DataFrame) -> None:
    """Request bị chuyển về trang đăng nhập (metric perftool_session_lost của script k6, cùng nhãn, cùng giây hoặc
    giây liền trước) -> tính là LỖI, trạng thái "SESSION_LOST"."""
    lost = lost.assign(ts=pd.to_numeric(lost["timestamp"], errors="coerce"), name=lost["name"].fillna("").astype(str))
    by_label: dict[str, list] = {}
    for idx, (lb, ts) in enumerate(zip(out["label"], out["ts"])):
        by_label.setdefault(lb, []).append((ts, idx))
    used: set[int] = set()
    for lb, ts in zip(lost["name"], lost["ts"]):
        cands = [(abs(t - ts), i) for t, i in by_label.get(lb, []) if i not in used and ts - 1 <= t <= ts + 1]
        if cands:
            i = min(cands)[1]
            used.add(i)
            out.iat[i, out.columns.get_loc("success")] = False
            out.iat[i, out.columns.get_loc("status")] = "SESSION_LOST"


def parse_jtl(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
    if "timeStamp" not in df.columns:
        raise ValueError("File JTL không đúng định dạng CSV (thiếu cột timeStamp)")
    # dòng cụt (JMeter bị dừng giữa lúc ghi): thiếu nhãn / kết quả -> bỏ, không tính thành 1 request lỗi của UC rỗng
    df = df.dropna(subset=[c for c in ("label", "elapsed", "success") if c in df.columns])
    df = df[df["success"].astype(str).str.lower().isin(["true", "false"])] if "success" in df.columns else df
    df = df[~df["label"].fillna("").str.endswith(SEP + "TRANSACTION")]
    out = pd.DataFrame({
        "ts": pd.to_numeric(df["timeStamp"], errors="coerce") / 1000.0,
        "label": df["label"].fillna("").astype(str),
        "elapsed_ms": pd.to_numeric(df["elapsed"], errors="coerce").astype(float),
        "success": df["success"].astype(str).str.lower().eq("true"),
        # assertion "mất phiên" của kế hoạch JMeter -> cùng mã SESSION_LOST như k6 (thay cho mã HTTP 200 dễ hiểu nhầm)
        "status": df.get("responseCode", pd.Series("", index=df.index)).fillna("").astype(str).where(
            ~df.get("failureMessage", pd.Series("", index=df.index)).fillna("").astype(str).str.startswith(
                "SESSION_LOST"), "SESSION_LOST"),
        "bytes": pd.to_numeric(df.get("bytes", pd.Series(np.nan, index=df.index)), errors="coerce"),
        "threads": pd.to_numeric(df.get("allThreads", pd.Series(np.nan, index=df.index)), errors="coerce"),
    })
    # số VU đi cùng dòng qua _finish (bỏ dòng hỏng/cụt khi bị dừng, sắp theo thời gian) để không lệch hàng
    out = _finish(out)
    if "allThreads" in df.columns:
        vus = pd.DataFrame({"ts": out["ts"].round(0), "vus": out["threads"]})
        vus = vus.groupby("ts", as_index=False)["vus"].max()
    else:
        vus = pd.DataFrame(columns=["ts", "vus"])
    return out.drop(columns="threads"), vus


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["ts", "elapsed_ms"]).copy()
    df["uc"] = _uc_of(df["label"].astype(str))
    df["is_login"] = df["label"].astype(str).str.endswith(SEP + "LOGIN")
    return df.sort_values("ts").reset_index(drop=True)


def load_results(tool: str, raw_file: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = Path(raw_file)
    if not p.exists() or p.stat().st_size == 0:
        raise FileNotFoundError(f"Chưa có dữ liệu kết quả: {p}")
    return parse_k6_csv(p) if tool == "k6" else parse_jtl(p)
