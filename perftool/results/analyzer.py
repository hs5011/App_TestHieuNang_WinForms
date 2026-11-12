"""Phân tích một lượt chạy: tính chỉ số, đánh giá ngưỡng, sinh nhận xét và lưu cache."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from ..models import Project, RunInfo, TestConfig
from ..scriptgen.profile import split_vus
from . import insights
from .metrics import evaluate, per_label, per_uc, summarize, timeseries
from .parsers import load_results

ANALYSIS_VERSION = 3   # tăng khi đổi câu chữ/cấu trúc nhận định để cache cũ tự tính lại


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return o.item()
    return o


def analyze_run(project: Project, run: RunInfo, force: bool = False) -> dict:
    run_dir = Path(run.script_file).parent
    cache = run_dir / "analysis.json"
    cfg = run.config or project.test_config.model_dump()
    p95_default = float(cfg.get("p95_threshold_ms", 3000))
    err_thr = float(cfg.get("error_rate_threshold", 0.01))
    thr_map = {sc.uc_code: (sc.p95_threshold_ms or p95_default) for sc in project.scenarios}
    # ngưỡng p95 riêng của UC có thể đổi ở bước 5 SAU khi chạy -> là một phần khoá cache (đổi ngưỡng = tính lại)
    thr_key = json.dumps({"p95": p95_default, "err": err_thr, "uc": thr_map}, sort_keys=True, ensure_ascii=False)
    if cache.exists() and not force and cache.stat().st_mtime >= Path(run.raw_file).stat().st_mtime:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            cached = {}
        if cached.get("version") == ANALYSIS_VERSION and cached.get("thr_key") == thr_key:
            return cached

    df, vus = load_results(run.tool, run.raw_file)
    uc_override = {sc.uc_code for sc in project.scenarios if sc.p95_threshold_ms}
    # nguồn ngưỡng: "uc" = đặt riêng cho UC (Bước 5), "default" = giá trị mặc định của công cụ, "global" = ngưỡng chung đã chỉnh (Bước 7)
    global_src = "default" if p95_default == TestConfig().p95_threshold_ms else "global"

    # lượt chạy gộp: mỗi UC chỉ nhận 1 phần số VU (chia đều theo thứ tự kịch bản, như lúc sinh script)
    per = per_uc(df, vus)
    uc_vus: dict[str, int] = {}
    if cfg.get("run_mode") == "combined" and len(per) > 1:
        order = [sc.uc_code for sc in project.scenarios if sc.uc_code in per] or list(per)
        order += [c for c in per if c not in order]
        uc_vus = dict(zip(order, split_vus(int(cfg.get("vus") or len(order)), len(order))))
    labels = per_label(df)
    labels.to_csv(run_dir / "per_label.csv", index=False, encoding="utf-8-sig")
    ucs: dict[str, dict] = {}
    for code, summ in per.items():
        if code in uc_vus:
            summ["uc_vus"] = uc_vus[code]
        g = df[(df["uc"] == code) & (~df["is_login"])]
        ts = timeseries(g, vus)
        ts.to_csv(run_dir / f"ts_{_safe(code)}.csv", index=False)
        thr = thr_map.get(code, p95_default)
        thr_src = "uc" if code in uc_override else global_src
        ev = evaluate(summ, thr, err_thr)
        lab = labels[labels["uc"] == code] if not labels.empty else labels
        uc = project.uc(code)
        ucs[code] = {
            "uc_code": code, "uc_name": uc.name if uc else code, "module": uc.module if uc else "",
            "summary": summ, "evaluation": ev, "p95_threshold": thr, "p95_threshold_src": thr_src,
            "error_threshold": err_thr, "verdict": insights.verdict_text(summ, ev, thr),
            "insights": insights.analyze(summ, ev, ts, lab, thr, err_thr, uc_vus.get(code) or cfg.get("vus"), thr_src),
            "labels": lab.to_dict("records") if not lab.empty else [],
            "ts_file": str(run_dir / f"ts_{_safe(code)}.csv"),
        }
    overall = summarize(df[~df["is_login"]], vus)
    ts_all = timeseries(df[~df["is_login"]], vus)
    ts_all.to_csv(run_dir / "ts_ALL.csv", index=False)
    result = {
        "version": ANALYSIS_VERSION, "thr_key": thr_key, "run_id": run.run_id, "tool": run.tool, "scenario_type": run.scenario_type, "config": cfg,
        "started_at": run.started_at, "finished_at": run.finished_at, "raw_file": run.raw_file,
        "overall": overall, "overall_eval": evaluate(overall, p95_default, err_thr), "ucs": ucs,
        "login": summarize(df[df["is_login"]]) if df["is_login"].any() else None,
    }
    result = _clean(result)
    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def load_ts(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def analyze_all(project: Project, force: bool = False) -> list[dict]:
    out = []
    for run in project.runs:
        if run.status != "done":
            continue
        try:
            out.append(analyze_run(project, run, force))
        except Exception as e:  # noqa: BLE001
            out.append({"run_id": run.run_id, "tool": run.tool, "error": str(e), "ucs": {}})
    return out


def flatten(results: list[dict]) -> list[dict]:
    """Mỗi phần tử = 1 (lượt chạy, UC) để lập bảng tổng hợp/báo cáo."""
    rows = []
    for r in results:
        for code, u in r.get("ucs", {}).items():
            rows.append({**u, "run_id": r["run_id"], "tool": r["tool"], "scenario_type": r["scenario_type"],
                         "config": r["config"], "raw_file": r.get("raw_file", ""),
                         "started_at": r.get("started_at", ""), "finished_at": r.get("finished_at", "")})
    return rows


def _safe(code: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in code)


def find_run(project: Project, run_id: str) -> Optional[RunInfo]:
    return next((r for r in project.runs if r.run_id == run_id), None)
