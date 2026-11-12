"""Sinh toàn bộ biểu đồ cho kết quả phân tích (dùng chung cho UI và báo cáo)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from ..models import Project
from ..scriptgen.profile import scenario_name
from ..results.analyzer import load_ts
from . import charts

CHART_TITLES = {
    "percentile": "Phân vị thời gian phản hồi so với ngưỡng đánh giá",
    "timeline": "Diễn biến thời gian phản hồi trong phiên bắn tải",
    "throughput": "Thông lượng theo thời gian",
    "vus": "Số người dùng đồng thời theo thời gian",
    "errors": "Tỷ lệ lỗi theo thời gian",
    "api": "Thời gian phản hồi p95 theo từng request/API",
    "resources": "Tài nguyên máy chủ (CPU, RAM, I/O, kết nối CSDL) trong thời gian kiểm thử",
}


def tool_name(tool: str) -> str:
    return "Apache JMeter" if tool == "jmeter" else "k6"


def build_for_row(row: dict, out_dir: Path, mon: Optional[pd.DataFrame] = None) -> dict[str, str]:
    """row = 1 phần tử của analyzer.flatten(): trả về {loại: đường dẫn PNG}."""
    s, ev = row["summary"], row["evaluation"]
    code = row["uc_code"]
    vus = row["config"].get("vus")
    sub = f"{code} — {scenario_name(row['scenario_type'])} ({tool_name(row['tool'])}, {vus} người dùng ảo)"
    base = out_dir / f"{row['run_id']}_{_safe(code)}"
    ts = load_ts(row.get("ts_file", ""))
    out: dict[str, str] = {}
    p = charts.percentile_chart(s, row["p95_threshold"], ev["passed"], sub, ev["reasons"], Path(f"{base}_pct.png"))
    out["percentile"] = str(p)
    sub_ts = f"{code} — {scenario_name(row['scenario_type'])}, {s.get('samples', 0)} mẫu trong {s.get('duration_s', 0):.0f} giây"
    for key, fn, args in (
        ("timeline", charts.timeline_chart, (ts, row["p95_threshold"], sub_ts)),
        ("throughput", charts.throughput_chart, (ts, sub_ts)),
        ("vus", charts.vus_chart, (ts, sub_ts)),
        ("errors", charts.error_chart, (ts, row["error_threshold"], sub_ts)),
        ("api", charts.api_p95_chart, (row.get("labels") or [], row["p95_threshold"], sub)),
    ):
        r = fn(*args, Path(f"{base}_{key}.png"))
        if r:
            out[key] = str(r)
    if mon is not None and not mon.empty:
        from ..monitor.store import run_slice
        sl = run_slice(mon, row.get("started_at", ""), row.get("finished_at", ""))
        r = charts.resource_chart(sl, f"{code} — {scenario_name(row['scenario_type'])} · "
                                      f"{row.get('started_at', '')} → {row.get('finished_at', '')[11:]}",
                                  Path(f"{base}_resources.png"))
        if r:
            out["resources"] = str(r)
    return out


def build_all(project: Project, rows: list[dict], out_dir: Path) -> tuple[dict[str, dict[str, str]], str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    mon = None
    if project.monitor.enabled:
        from ..monitor.store import load_all
        mon = load_all(project.id)
    per_row = {f"{r['run_id']}|{r['uc_code']}": build_for_row(r, out_dir, mon) for r in rows}
    cmp = charts.comparison_chart(rows, out_dir / "comparison.png")
    return per_row, str(cmp) if cmp else ""


def _safe(code: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in code)


OVERVIEW_TITLES = {
    "vus": "Số người dùng đồng thời theo thời gian – tổng quan các kịch bản",
    "p95": "Thời gian phản hồi p95 theo thời gian – so sánh các kịch bản",
    "throughput": "Thông lượng trung bình theo kịch bản",
    "comparison": "So sánh thời gian phản hồi p95 giữa các kịch bản và ngưỡng đánh giá",
    "modules": "Thời gian phản hồi p95 cao nhất theo phân hệ",
}


def build_overview(rows: list[dict], out_dir: Path) -> dict[str, str]:
    """Biểu đồ tổng quát khi báo cáo có nhiều kịch bản (loại kịch bản × công cụ × UC)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    ts_cache = {id(r): load_ts(r.get("ts_file", "")) for r in rows}

    # 1) VUs theo thời gian: mỗi (loại kịch bản, công cụ) 1 đường (các UC cùng loại có cùng hồ sơ tải)
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["scenario_type"], r["tool"])
        if key not in best or r["summary"].get("samples", 0) > best[key]["summary"].get("samples", 0):
            best[key] = r
    order = ["smoke", "load", "stress", "spike", "soak"]
    series = []
    for (typ, tool), r in sorted(best.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 9, kv[0][1])):
        ts = ts_cache[id(r)]
        if ts.empty or ts["vus"].isna().all():
            continue
        ts = ts.dropna(subset=["vus"])
        series.append({"label": f"{scenario_name(typ)} · {tool_name(tool)} ({int(ts['vus'].max())} VUs)", "tool": tool,
                       "t": ts["t"].tolist(), "vus": ts["vus"].tolist()})
    p = charts.overview_vus_chart(series, out_dir / "overview_vus.png")
    if p:
        out["vus"] = str(p)

    # 2) p95 theo thời gian: mỗi loại kịch bản 1 khung, mỗi UC × công cụ 1 đường
    groups: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (order.index(r["scenario_type"]) if r["scenario_type"] in order else 9,
                                         r["uc_code"], r["tool"])):
        ts = ts_cache[id(r)]
        if ts.empty:
            continue
        groups.setdefault(scenario_name(r["scenario_type"]), []).append({
            "label": f"{r['uc_code']} · {tool_name(r['tool'])}", "tool": r["tool"], "color_key": r["uc_code"],
            "t": ts["t"].tolist(), "p95": ts["p95"].fillna(0).tolist()})
    thr = sorted(r["p95_threshold"] for r in rows)[len(rows) // 2]
    p = charts.overview_p95_chart(groups, thr, out_dir / "overview_p95.png")
    if p:
        out["p95"] = str(p)

    # 2b) theo phân hệ
    from ..results.insights import module_summaries
    mods = module_summaries(rows)
    if len(mods) >= 1:
        p = charts.module_p95_chart(mods, out_dir / "overview_modules.png")
        if p:
            out["modules"] = str(p)

    # 3) thông lượng và 4) so sánh p95 với ngưỡng
    p = charts.overview_throughput_chart(rows, out_dir / "overview_throughput.png")
    if p:
        out["throughput"] = str(p)
    p = charts.comparison_chart(rows, out_dir / "comparison.png")
    if p:
        out["comparison"] = str(p)
    return out
