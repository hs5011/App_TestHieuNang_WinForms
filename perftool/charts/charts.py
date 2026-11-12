"""Vẽ biểu đồ PNG (matplotlib) theo phong cách mẫu báo cáo.

Quy ước: tiêu đề đậm căn trái + phụ đề xám, lưới ngang nhạt, không dùng 2 trục Y,
màu trạng thái Đạt/Không đạt luôn đi kèm nhãn chữ.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from ..scriptgen.profile import scenario_name  # noqa: E402

# Bảng màu (khớp mẫu báo cáo)
INK = "#1F2328"
MUTED = "#6E7781"
GRID = "#E6E8EB"
BLUE = "#1F4E9C"      # p95 / series chính
GREEN = "#4DA66A"     # trung bình
RED = "#C00000"       # ngưỡng / không đạt
RED_FILL = "#E27575"
GREEN_DARK = "#2E7D32"
GREEN_FILL = "#7BC08A"
ORANGE = "#D9822B"

plt.rcParams.update({
    "font.family": ["Segoe UI", "Arial", "DejaVu Sans"],
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": MUTED,
    "xtick.color": INK,
    "ytick.color": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "figure.dpi": 110,
})

W, H = 9.0, 4.2


def _frame(title: str, subtitle: str, ylabel: str = "mili giây (ms)", h: float = H):
    fig, ax = plt.subplots(figsize=(W, h))
    # khoảng cách tính theo inch để không phụ thuộc chiều cao hình (tránh nhãn trục đè lên số trên cùng)
    top_in = 1.25 if ylabel else 1.0
    fig.subplots_adjust(left=0.08, right=0.98, top=1 - top_in / h, bottom=0.17)
    fig.text(0.025, 1 - 0.18 / h, title, fontsize=13, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.025, 1 - 0.46 / h, subtitle, fontsize=9.5, color=MUTED, ha="left", va="top")
    if ylabel:
        fig.text(0.025, 1 - 0.76 / h, ylabel, fontsize=9, color=MUTED, ha="left", va="top")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_color("#8C959F")
    ax.tick_params(axis="y", length=0)
    return fig, ax


def _save(fig, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def percentile_chart(summary: dict, threshold: float, passed: bool, subtitle: str, reasons: list[str],
                     out: Path) -> Path:
    """Biểu đồ cột: Trung bình, p50, p90, p95, p99, Tối đa so với ngưỡng đánh giá p95."""
    keys = [("avg", "Trung bình"), ("p50", "p50"), ("p90", "p90"), ("p95", "p95"), ("p99", "p99"), ("max", "Tối đa")]
    vals = [float(summary.get(k) or 0) for k, _ in keys]
    fig, ax = _frame("Thời gian phản hồi theo phân vị", subtitle)
    face, edge = (GREEN_FILL, GREEN_DARK) if passed else (RED_FILL, RED)
    bars = ax.bar([lbl for _, lbl in keys], vals, width=0.52, color=face, edgecolor=edge, linewidth=1.4)
    top = max(vals + [threshold]) * 1.18 or 1
    ax.set_ylim(0, top)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + top * 0.012, f"{v:.0f}", ha="center", va="bottom",
                fontsize=9.5, fontweight="bold", color=edge)
    ax.axhline(threshold, color=BLUE, linestyle=(0, (5, 3)), linewidth=1.6)
    ax.text(len(keys) - 0.5, threshold + top * 0.015, f"Ngưỡng đánh giá p95 = {threshold:.0f} ms", ha="right",
            va="bottom", fontsize=9, color=BLUE, bbox=dict(facecolor="white", edgecolor="none", pad=1.5))
    concl = "KẾT LUẬN: ĐẠT" if passed else "KẾT LUẬN: KHÔNG ĐẠT"
    detail = (f"p95 {summary.get('p95', 0):.0f} ms ≤ ngưỡng {threshold:.0f} ms" if passed
              else "; ".join(reasons))
    fig.text(0.025, 0.03, concl, fontsize=10.5, fontweight="bold", color=GREEN_DARK if passed else RED)
    fig.text(0.215 if not passed else 0.18, 0.03, detail, fontsize=9, color=MUTED)
    return _save(fig, out)


def timeline_chart(ts: pd.DataFrame, threshold: float, subtitle: str, out: Path) -> Optional[Path]:
    """Diễn biến thời gian phản hồi (trung bình, p95) theo thời gian + vạch lỗi."""
    if ts is None or ts.empty:
        return None
    fig, ax = _frame("Diễn biến thời gian phản hồi trong phiên bắn tải", subtitle)
    ax.plot(ts["t"], ts["avg"], color=GREEN, linewidth=1.6, label="Trung bình")
    ax.plot(ts["t"], ts["p95"], color=BLUE, linewidth=1.6, label="p95")
    ax.axhline(threshold, color=RED, linestyle=(0, (5, 3)), linewidth=1.6, label="Ngưỡng p95")
    top = max(float(ts["p95"].max()), threshold) * 1.2
    ax.set_ylim(0, top)
    err = ts[ts.get("errors", pd.Series(0, index=ts.index)) > 0]
    if len(err):
        width = max(float(ts["t"].diff().median() or 1) * 0.5, 1)
        ax.bar(err["t"], top * 0.03, width=width, bottom=0, color=RED_FILL, label="Có lỗi")
    ax.set_xlim(0, float(ts["t"].max()) or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    ax.legend(loc="upper left", bbox_to_anchor=(-0.07, -0.08), ncol=4, frameon=False, fontsize=9)
    fig.subplots_adjust(bottom=0.2)
    return _save(fig, out)


def throughput_chart(ts: pd.DataFrame, subtitle: str, out: Path) -> Optional[Path]:
    if ts is None or ts.empty:
        return None
    fig, ax = _frame("Thông lượng theo thời gian", subtitle, ylabel="yêu cầu/giây (req/s)")
    ax.plot(ts["t"], ts["rps"], color=BLUE, linewidth=1.8)
    ax.fill_between(ts["t"], ts["rps"], color=BLUE, alpha=0.08)
    ax.set_ylim(0, float(ts["rps"].max()) * 1.2 or 1)
    ax.set_xlim(0, float(ts["t"].max()) or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    return _save(fig, out)


def vus_chart(ts: pd.DataFrame, subtitle: str, out: Path) -> Optional[Path]:
    if ts is None or ts.empty or ts["vus"].isna().all():
        return None
    fig, ax = _frame("Số người dùng đồng thời", subtitle, ylabel="người dùng ảo (VUs)")
    ax.step(ts["t"], ts["vus"], where="post", color=ORANGE, linewidth=1.8)
    ax.set_ylim(0, float(ts["vus"].max()) * 1.2 or 1)
    ax.set_xlim(0, float(ts["t"].max()) or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    return _save(fig, out)


def error_chart(ts: pd.DataFrame, threshold: float, subtitle: str, out: Path) -> Optional[Path]:
    if ts is None or ts.empty:
        return None
    fig, ax = _frame("Tỷ lệ lỗi theo thời gian", subtitle, ylabel="tỷ lệ lỗi (%)")
    ax.plot(ts["t"], ts["error_rate"] * 100, color=RED, linewidth=1.8)
    ax.axhline(threshold * 100, color=MUTED, linestyle=(0, (5, 3)), linewidth=1.2)
    ax.text(float(ts["t"].max()), threshold * 100, f"Ngưỡng {threshold * 100:.1f}%", ha="right", va="bottom",
            fontsize=9, color=MUTED)
    ax.set_ylim(0, max(float(ts["error_rate"].max()) * 100 * 1.2, threshold * 100 * 2, 1))
    ax.set_xlim(0, float(ts["t"].max()) or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    return _save(fig, out)


def api_p95_chart(labels: list[dict], threshold: float, subtitle: str, out: Path, top: int = 12) -> Optional[Path]:
    """Thanh ngang p95 theo từng request/API."""
    if not labels:
        return None
    rows = sorted(labels, key=lambda r: r.get("p95") or 0, reverse=True)[:top][::-1]
    names = [(r["label"].split(" :: ", 1)[-1])[:55] for r in rows]
    vals = [float(r.get("p95") or 0) for r in rows]
    h = max(2.8, 0.38 * len(rows) + 1.6)
    fig, ax = _frame("p95 theo từng request/API", subtitle, ylabel="", h=h)
    fig.subplots_adjust(left=0.36, bottom=0.12, top=1 - 1.05 / h)
    colors = [RED_FILL if v > threshold else GREEN_FILL for v in vals]
    ax.barh(names, vals, color=colors, height=0.6)
    ax.grid(axis="x", color=GRID)
    ax.grid(axis="y", visible=False)
    ax.axvline(threshold, color=BLUE, linestyle=(0, (5, 3)), linewidth=1.4)
    ax.text(threshold, len(vals) - 0.45, f" Ngưỡng p95 = {threshold:.0f} ms", color=BLUE, fontsize=8.5, va="bottom")
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:.0f} ms", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, max(vals + [threshold]) * 1.22)
    ax.tick_params(axis="y", labelsize=8.5, colors=INK)
    return _save(fig, out)


def run_time(r: dict) -> str:
    """Giờ chạy lấy từ run_id dạng 20260925-124359_k6_smoke_UC-1 -> '12:43:59'."""
    rid = str(r.get("run_id") or "")
    m = re.match(r"\d{8}-(\d{2})(\d{2})(\d{2})", rid)
    return f"{m[1]}:{m[2]}:{m[3]}" if m else rid[:15]


def run_labels(rows: list[dict], sep: str = " · ") -> list[str]:
    """Nhãn 'UC · công cụ · kịch bản' cho từng dòng; nhãn trùng (chạy lại cùng kịch bản) được thêm giờ chạy."""
    base = [f"{r['uc_code']}{sep}{r['tool']} · {scenario_name(r['scenario_type'])}" for r in rows]
    out = [b + (f" · {run_time(r)}" if base.count(b) > 1 else "") for b, r in zip(base, rows)]
    return [o + (f" #{i + 1}" if out.count(o) > 1 else "") for i, o in enumerate(out)]


def comparison_chart(rows: list[dict], out: Path) -> Optional[Path]:
    """So sánh p95 của các kịch bản (UC × công cụ) với ngưỡng."""
    if not rows:
        return None
    vals = [float(r["summary"].get("p95") or 0) for r in rows]
    thr = [float(r["p95_threshold"]) for r in rows]
    if len(rows) > 8:
        return _comparison_h(rows, vals, thr, out)
    names = run_labels(rows, sep="\n")
    fig, ax = _frame("So sánh p95 giữa các kịch bản", "Cột xanh: đạt ngưỡng · cột đỏ: vượt ngưỡng · vạch: ngưỡng từng UC",
                     h=max(H, 3.4))
    colors = [GREEN_FILL if v <= t else RED_FILL for v, t in zip(vals, thr)]
    x = range(len(rows))
    ax.bar(x, vals, color=colors, width=0.55)
    for i, (v, t) in enumerate(zip(vals, thr)):
        ax.hlines(t, i - 0.35, i + 0.35, color=BLUE, linestyle=(0, (4, 2)), linewidth=1.5)
        ax.text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold", color=INK)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylim(0, max(vals + thr) * 1.2 or 1)
    fig.subplots_adjust(bottom=0.22)
    return _save(fig, out)


def _comparison_h(rows: list[dict], vals: list[float], thr: list[float], out: Path) -> Optional[Path]:
    """Nhiều kịch bản (> 8): thanh ngang, mỗi kịch bản 1 hàng, nhãn 1 dòng – không chồng chữ như cột đứng."""
    names = run_labels(rows)
    n = len(rows)
    h = max(H, 1.6 + 0.32 * n)
    fig, ax = _frame("So sánh p95 giữa các kịch bản", "Thanh xanh: đạt ngưỡng · thanh đỏ: vượt ngưỡng · vạch: ngưỡng từng UC",
                     ylabel="", h=h)
    fig.subplots_adjust(left=0.36, bottom=0.6 / h)
    y = list(range(n))[::-1]
    colors = [GREEN_FILL if v <= t else RED_FILL for v, t in zip(vals, thr)]
    ax.barh(y, vals, color=colors, height=0.6)
    top = max(vals + thr) * 1.15 or 1
    for yi, v, t in zip(y, vals, thr):
        ax.vlines(t, yi - 0.38, yi + 0.38, color=BLUE, linestyle=(0, (4, 2)), linewidth=1.5)
        ax.text(v + top * 0.01, yi, f"{v:.0f}", va="center", ha="left", fontsize=8, fontweight="bold", color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlim(0, top)
    ax.set_xlabel("p95 (ms)", fontsize=9, color=MUTED)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    return _save(fig, out)


def histogram_chart(values: pd.Series, threshold: float, subtitle: str, out: Path) -> Optional[Path]:
    if values is None or values.empty:
        return None
    fig, ax = _frame("Phân bố thời gian phản hồi", subtitle, ylabel="số request")
    clip = values.quantile(0.995)
    ax.hist(values.clip(upper=clip), bins=40, color=BLUE, alpha=0.75, edgecolor="white", linewidth=0.8)
    ax.axvline(threshold, color=RED, linestyle=(0, (5, 3)), linewidth=1.5)
    ax.text(threshold, ax.get_ylim()[1] * 0.95, f" Ngưỡng {threshold:.0f} ms", color=RED, fontsize=9, va="top")
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}ms"))
    return _save(fig, out)


# ============================================================ biểu đồ tổng quát nhiều kịch bản
# Bảng màu phân loại cố định (không xoay vòng); công cụ phân biệt bằng kiểu nét: k6 nét liền, JMeter nét đứt
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TOOL_STYLE = {"k6": "-", "jmeter": (0, (5, 2))}


def _legend_below(ax, ncol: int) -> None:
    ax.legend(loc="upper left", bbox_to_anchor=(-0.02, -0.14), ncol=ncol, frameon=False, fontsize=8.5,
              handlelength=2.6)


def overview_vus_chart(series: list[dict], out: Path) -> Optional[Path]:
    """Số VU đồng thời theo thời gian của các kịch bản. series: [{label, tool, t: [...], vus: [...]}]"""
    series = [s for s in series if len(s["t"])]
    if not series:
        return None
    fig, ax = _frame("Số người dùng đồng thời theo thời gian – tổng quan các kịch bản",
                     "Đo thực tế trong từng lượt chạy · nét liền: k6 · nét đứt: JMeter", ylabel="người dùng ảo (VUs)",
                     h=4.6)
    top = 1
    for i, s in enumerate(series[:len(SERIES)]):
        ax.plot(s["t"], s["vus"], color=SERIES[i], linestyle=TOOL_STYLE.get(s["tool"], "-"), linewidth=2,
                label=s["label"], solid_joinstyle="round")
        top = max(top, max(s["vus"]))
    ax.set_ylim(0, top * 1.2)
    ax.set_xlim(0, max(max(s["t"]) for s in series) or 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    _legend_below(ax, min(4, len(series)))
    fig.subplots_adjust(bottom=0.27)
    return _save(fig, out)


def overview_p95_chart(groups: dict[str, list[dict]], threshold: float, out: Path) -> Optional[Path]:
    """p95 theo thời gian, mỗi loại kịch bản 1 khung (small multiples). groups: {loại: [{label, tool, color_key, t, p95}]}"""
    groups = {k: [s for s in v if len(s["t"])] for k, v in groups.items()}
    groups = {k: v for k, v in groups.items() if v}
    if not groups:
        return None
    n = len(groups)
    fig, axes = plt.subplots(n, 1, figsize=(W, 2.5 * n + 1.3), squeeze=False)
    fig.text(0.025, 1 - 0.28 / (2.5 * n + 1.3), "Thời gian phản hồi p95 theo thời gian – so sánh các kịch bản",
             fontsize=13, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.025, 1 - 0.62 / (2.5 * n + 1.3), "Mỗi khung 1 loại kịch bản · màu theo UC · nét liền: k6, nét đứt: "
             f"JMeter · vạch đỏ: ngưỡng p95 {threshold:.0f} ms", fontsize=9.5, color=MUTED, ha="left", va="top")
    keys = sorted({s["color_key"] for v in groups.values() for s in v})
    color_of = {k: SERIES[i % len(SERIES)] for i, k in enumerate(keys)}
    for ax, (typ, ss) in zip(axes[:, 0], groups.items()):
        ymax = max(max(max(s["p95"]) for s in ss), threshold) * 1.15   # thang riêng từng khung
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_color("#8C959F")
        ax.tick_params(axis="y", length=0)
        for s in ss:
            ax.plot(s["t"], s["p95"], color=color_of[s["color_key"]], linestyle=TOOL_STYLE.get(s["tool"], "-"),
                    linewidth=1.6, label=s["label"])
        ax.axhline(threshold, color=RED, linestyle=(0, (5, 3)), linewidth=1.2)
        ax.set_ylim(0, ymax)
        ax.set_xlim(0, max(max(s["t"]) for s in ss) or 1)
        ax.set_title(typ, loc="left", fontsize=10.5, color=INK, fontweight="bold")
        ax.set_ylabel("ms", color=MUTED, fontsize=9)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    handles, labels = [], []
    for ax in axes[:, 0]:
        for h, l in zip(*ax.get_legend_handles_labels()):
            if l not in labels:
                handles.append(h)
                labels.append(l)
    fig.legend(handles, labels, loc="lower left", bbox_to_anchor=(0.02, 0.0), ncol=min(3, len(labels)),
               frameon=False, fontsize=8.5, handlelength=2.6)
    rows_leg = (len(labels) + 2) // 3
    fig.subplots_adjust(left=0.08, right=0.98, top=1 - 1.05 / (2.5 * n + 1.3),
                        bottom=(0.35 + 0.22 * rows_leg) / (2.5 * n + 1.3), hspace=0.55)
    return _save(fig, out)


def overview_throughput_chart(rows: list[dict], out: Path) -> Optional[Path]:
    """Thông lượng trung bình và số VU của từng kịch bản (cột ngang, xếp theo loại kịch bản)."""
    if not rows:
        return None
    labels = run_labels(rows)[::-1]
    vals = [float(r["summary"].get("throughput") or 0) for r in rows][::-1]
    vus = [r["summary"].get("max_vus") or r["config"].get("vus") for r in rows][::-1]
    h = max(2.8, 0.34 * len(rows) + 1.6)
    fig, ax = _frame("Thông lượng trung bình theo kịch bản", "yêu cầu/giây (req/s) · số trong ngoặc: VUs tối đa",
                     ylabel="", h=h)
    fig.subplots_adjust(left=0.3, bottom=0.1, top=1 - 1.05 / h)
    # vẽ theo vị trí số (không theo nhãn) để 2 dòng trùng nhãn không bị gộp chung 1 cột
    ax.barh(range(len(vals)), vals, color=SERIES[0], height=0.6)
    ax.set_yticks(range(len(vals)), labels)
    ax.set_ylim(-0.6, len(vals) - 0.4)
    ax.grid(axis="x", color=GRID)
    ax.grid(axis="y", visible=False)
    for i, (v, u) in enumerate(zip(vals, vus)):
        ax.text(v, i, f" {v:.1f} ({u} VUs)", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, max(vals) * 1.3 or 1)
    ax.tick_params(axis="y", labelsize=8.5, colors=INK)
    return _save(fig, out)


VERDICT_COLOR = {"Đạt": GREEN_FILL, "Đạt một phần": "#F2B84B", "Không đạt": RED_FILL}


def module_p95_chart(summaries: list[dict], out: Path) -> Optional[Path]:
    """p95 cao nhất của từng phân hệ so với ngưỡng; màu theo kết luận phân hệ (kèm nhãn chữ)."""
    if not summaries:
        return None
    rows = sorted(summaries, key=lambda s: s["p95_max"])
    names = [(s["module"][:48] + "…") if len(s["module"]) > 49 else s["module"] for s in rows]
    vals = [float(s["p95_max"]) for s in rows]
    thr = max(float(s["threshold"]) for s in rows)
    h = max(2.8, 0.42 * len(rows) + 1.7)
    fig, ax = _frame("Thời gian phản hồi p95 cao nhất theo phân hệ",
                     "Màu theo kết luận phân hệ · số trong ngoặc: số kịch bản đạt / tổng · vạch xanh: ngưỡng p95",
                     ylabel="", h=h)
    fig.subplots_adjust(left=0.38, bottom=0.2 / h * 4, top=1 - 1.05 / h)
    ax.barh(names, vals, color=[VERDICT_COLOR[s["verdict"]] for s in rows], height=0.6)
    ax.grid(axis="x", color=GRID)
    ax.grid(axis="y", visible=False)
    ax.axvline(thr, color=BLUE, linestyle=(0, (5, 3)), linewidth=1.4)
    ax.text(thr, len(rows) - 0.45, f" Ngưỡng {thr:.0f} ms", color=BLUE, fontsize=8.5, va="bottom")
    for i, s in enumerate(rows):
        ax.text(vals[i], i, f" {vals[i]:.0f} ms · {s['verdict']} ({s['passed']}/{s['runs']})", va="center",
                fontsize=8.5, color=INK)
    ax.set_xlim(0, max(vals + [thr]) * 1.45)
    ax.tick_params(axis="y", labelsize=8.5, colors=INK)
    return _save(fig, out)


SERVER_COLORS = [BLUE, ORANGE, GREEN_DARK, "#8250DF", "#BF3989", "#1B7C83", MUTED]
RESOURCE_PANELS = [   # (tiêu đề khung, [(metric, nhãn)], đơn vị, trần trục Y cố định)
    ("CPU", [("cpu", "")], "%", 100),
    ("RAM", [("ram", "")], "%", 100),
    ("Ổ đĩa bận", [("disk_busy", "")], "%", 100),
    ("Đọc / ghi đĩa", [("disk_read", "đọc"), ("disk_write", "ghi")], "MB/s", None),
    ("Mạng", [("net", "")], "MB/s", None),
    ("Kết nối CSDL", [("db_conn", "")], "kết nối", None),
]


def resource_chart(s: pd.DataFrame, subtitle: str, out: Path) -> Optional[Path]:
    """Tài nguyên máy chủ theo thời gian: mỗi chỉ số 1 khung nhỏ, mỗi máy chủ 1 màu (kèm chú thích tên)."""
    if s is None or s.empty:
        return None
    present = set(s["metric"])
    panels = [pn for pn in RESOURCE_PANELS if any(m in present for m, _ in pn[1])]
    if not panels:
        return None
    servers = list(dict.fromkeys(s["server"]))
    color = {sv: SERVER_COLORS[i % len(SERVER_COLORS)] for i, sv in enumerate(servers)}
    ncol = 2 if len(panels) > 1 else 1
    nrow = (len(panels) + ncol - 1) // ncol
    h = 1.35 + 2.1 * nrow
    fig, axes = plt.subplots(nrow, ncol, figsize=(W, h), squeeze=False, sharex=True)
    fig.subplots_adjust(left=0.07, right=0.98, top=1 - 1.0 / h, bottom=0.55 / h + 0.05, hspace=0.55, wspace=0.18)
    fig.text(0.025, 1 - 0.18 / h, "Tài nguyên máy chủ trong thời gian kiểm thử", fontsize=13, fontweight="bold",
             color=INK, ha="left", va="top")
    fig.text(0.025, 1 - 0.46 / h, subtitle, fontsize=9.5, color=MUTED, ha="left", va="top")
    tmax = float(s["t"].max()) or 1
    for ax, (title, series, unit, ymax) in zip(axes.flat, panels):
        top = 0.0
        for metric, suffix in series:
            g = s[s["metric"] == metric]
            for sv, gs in g.groupby("server", sort=False):
                ax.plot(gs["t"], gs["value"], color=color.get(sv, BLUE), linewidth=1.5,
                        linestyle="--" if suffix == "ghi" else "-")
                top = max(top, float(gs["value"].max()))
        ax.set_title(f"{title} ({unit})" + (" – nét liền: đọc, nét đứt: ghi" if len(series) > 1 else ""),
                     loc="left", fontsize=9.5, color=INK)
        ax.set_ylim(0, ymax if ymax else (top * 1.2 or 1))
        ax.set_xlim(0, tmax)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0, labelsize=8.5)
        ax.tick_params(axis="x", labelsize=8.5)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}s"))
    for ax in list(axes.flat)[len(panels):]:
        ax.axis("off")
    handles = [plt.Line2D([0], [0], color=color[sv], linewidth=2) for sv in servers]
    fig.legend(handles, servers, loc="lower left", bbox_to_anchor=(0.02, 0.0), ncol=min(len(servers), 5),
               frameon=False, fontsize=9)
    return _save(fig, out)
