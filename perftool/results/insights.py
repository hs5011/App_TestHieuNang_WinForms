"""Sinh nhận xét tự động (tiếng Việt) dựa trên số liệu thực tế."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..scriptgen.profile import scenario_name
from .metrics import fmt_ms, fmt_pct


_THR_SRC = {"default": " – mặc định của công cụ", "uc": " – đặt riêng cho UC", "global": ""}


def thr_ref(thr: float, src: Optional[str] = None) -> str:
    """Cụm 'ngưỡng p95 (3000 ms – mặc định của công cụ)' để người đọc biết ngưỡng lấy từ đâu."""
    return f"ngưỡng p95 ({thr:.0f} ms{_THR_SRC.get(src or '', '')})"


def pct_of_thr(row: dict) -> str:
    """'bằng 29% ngưỡng p95 (3000 ms – mặc định của công cụ)' cho một dòng kết quả."""
    p95 = row["summary"].get("p95") or 0
    return f"bằng {p95 / max(row['p95_threshold'], 1) * 100:.0f}% {thr_ref(row['p95_threshold'], row.get('p95_threshold_src'))}"


def verdict_text(summary: dict, ev: dict, p95_thr: float) -> str:
    """Câu 'Nhận định' theo phong cách mẫu báo cáo."""
    if summary.get("samples", 0) == 0:
        return "Nhận định: Không thu được dữ liệu đo, cần kiểm tra lại script/môi trường."
    tp = f"{summary['throughput']:.2f}"
    er = fmt_pct(summary.get("error_rate"))
    if ev["passed"]:
        return (f"Nhận định: Kịch bản đạt toàn bộ ngưỡng đánh giá. Thời gian phản hồi p95 {summary['p95']:.1f} ms "
                f"(ngưỡng {p95_thr:.0f} ms), tỷ lệ lỗi {er}, thông lượng {tp} yêu cầu/giây.")
    return f"Nhận định: Kịch bản KHÔNG đạt ngưỡng đánh giá: {'; '.join(ev['reasons'])}. Thông lượng {tp} yêu cầu/giây."


def analyze(summary: dict, ev: dict, ts: pd.DataFrame, labels: pd.DataFrame, p95_thr: float,
            err_thr: float, target_vus: Optional[int] = None, thr_src: Optional[str] = None) -> list[str]:
    out: list[str] = []
    if summary.get("samples", 0) == 0:
        return ["Không có mẫu đo nào được ghi nhận."]

    # 1. Tổng quan
    n = f"{summary['samples']:,}".replace(",", ".")
    out.append(f"Tổng cộng {n} request trong {summary['duration_s']:.0f} giây, thông lượng trung bình "
               f"{summary['throughput']:.2f} req/s " + (
                   f"với {summary['uc_vus']} người dùng ảo dành cho UC này (lượt chạy gộp, tổng tối đa "
                   f"{summary.get('max_vus', 0)} người dùng đồng thời)." if summary.get("uc_vus")
                   else f"với tối đa {summary.get('max_vus', 0)} người dùng đồng thời."))
    # 2. Độ trễ đuôi
    if summary.get("p50") and summary.get("p99"):
        ratio = summary["p99"] / max(summary["p50"], 1)
        if ratio >= 5:
            out.append(f"Phân bố thời gian phản hồi có 'đuôi dài': p99 ({fmt_ms(summary['p99'])}) gấp {ratio:.1f} lần p50 "
                       f"({fmt_ms(summary['p50'])}) – một nhóm request bị chậm bất thường, cần kiểm tra truy vấn/khoá dữ liệu.")
        elif ratio <= 2:
            out.append(f"Thời gian phản hồi đồng đều (p99/p50 = {ratio:.1f}), hệ thống xử lý ổn định.")
    # 3. So với ngưỡng
    if summary.get("p95") is not None:
        pct = summary["p95"] / p95_thr * 100 if p95_thr else 0
        if ev["p95_ok"]:
            out.append(f"p95 = {fmt_ms(summary['p95'])}, bằng {pct:.0f}% {thr_ref(p95_thr, thr_src)}"
                       + (" – còn dư địa lớn." if pct < 50 else " – tiệm cận ngưỡng, cần theo dõi khi tăng tải." if pct > 80 else "."))
        else:
            out.append(f"p95 = {fmt_ms(summary['p95'])}, vượt {pct - 100:.0f}% so với {thr_ref(p95_thr, thr_src)}.")
    # 4. Lỗi
    if summary.get("errors"):
        codes = ", ".join(f"{k or 'không phản hồi'}: {v}" for k, v in (summary.get("status_codes") or {}).items())
        out.append(f"Có {summary['errors']} request lỗi ({fmt_pct(summary['error_rate'])}). Phân bố mã lỗi: {codes}.")
        sc = {str(k): v for k, v in (summary.get("status_codes") or {}).items()}
        n429 = sc.get("429", 0) + sc.get("429.0", 0)
        if n429:
            api = ""
            if labels is not None and not labels.empty:
                top = labels.sort_values("errors", ascending=False).iloc[0]
                api = f" (tập trung ở {top['label'].split(' :: ', 1)[-1]})"
            out.append(f"{n429} lỗi là HTTP 429 Too Many Requests{api}: máy chủ/gateway đang giới hạn tần suất (rate limit), "
                       "thường theo tài khoản hoặc IP. Đây là cơ chế bảo vệ, không phải quá tải – cần kiểm thử với nhiều "
                       "tài khoản hoặc nới giới hạn cho môi trường kiểm thử để đo đúng năng lực xử lý.")
        if not ts.empty and ts["errors"].sum() > 0:
            first = ts[ts["errors"] > 0]["t"].min()
            vus_at = ts.loc[ts["t"] == first, "vus"].iloc[0] if "vus" in ts and not ts["vus"].isna().all() else None
            out.append(f"Lỗi bắt đầu xuất hiện từ giây thứ {first:.0f}"
                       + (f" (khoảng {vus_at:.0f} người dùng đồng thời)." if vus_at else "."))
    else:
        out.append("Không ghi nhận request lỗi trong suốt phiên kiểm thử.")
    # 5. Tương quan tải - thời gian phản hồi
    if not ts.empty and ts["vus"].notna().sum() > 5 and ts["vus"].nunique() > 2:
        corr = float(np.corrcoef(ts["vus"].fillna(0), ts["avg"].fillna(0))[0, 1])
        if corr >= 0.7:
            peak = ts["vus"].max()
            low = ts[ts["vus"] <= peak * 0.3]["avg"].mean()
            high = ts[ts["vus"] >= peak * 0.95]["avg"].mean()
            trend = (f": trung bình {low:.0f} ms khi ≤30% tải → {high:.0f} ms khi đủ tải"
                     if not np.isnan(low) and not np.isnan(high) else "")
            near = summary.get("p95") and p95_thr and summary["p95"] >= 0.8 * p95_thr
            out.append(f"Thời gian phản hồi tăng theo số người dùng đồng thời (hệ số tương quan {corr:.2f}){trend}. "
                       + ("p95 đã tiệm cận/vượt ngưỡng – dấu hiệu hệ thống chạm giới hạn tài nguyên (CPU/DB/connection pool)."
                          if near else "Vẫn còn cách xa ngưỡng đánh giá, nhưng cần theo dõi xu hướng này khi tăng tải."))
        elif corr <= 0.3:
            out.append(f"Thời gian phản hồi ít phụ thuộc vào số người dùng (tương quan {corr:.2f}) – khả năng mở rộng tốt trong dải tải đã thử.")
        # bão hoà thông lượng
        peak = ts["vus"].max()
        half = ts[(ts["vus"] >= peak * 0.4) & (ts["vus"] <= peak * 0.6)]["rps"].mean()
        full = ts[ts["vus"] >= peak * 0.95]["rps"].mean()
        if half and full and not np.isnan(half) and not np.isnan(full) and full < half * 1.2:
            out.append(f"Thông lượng gần như không tăng khi tải tăng từ ~50% lên 100% ({half:.1f} → {full:.1f} req/s): "
                       "hệ thống đã bão hoà, tăng người dùng chỉ làm tăng thời gian chờ.")
    # 6. API chậm nhất
    if labels is not None and not labels.empty:
        labels = labels[~labels["label"].str.endswith(" :: LOGIN")]
    if labels is not None and len(labels) > 1:
        top = labels.head(3)
        items = "; ".join(f"{r.label.split(' :: ', 1)[-1]} (p95 {fmt_ms(r.p95)})" for r in top.itertuples())
        out.append(f"Các request chậm nhất: {items}.")
    # 7. Ổn định
    if not ts.empty and len(ts) > 10:
        steady = ts.iloc[len(ts) // 4: len(ts) * 3 // 4]
        if steady["p95"].mean() > 0:
            cv = steady["p95"].std() / steady["p95"].mean()
            if cv > 0.5:
                out.append(f"Thời gian phản hồi dao động lớn trong giai đoạn giữ tải (CV = {cv:.2f}), có các đỉnh đột biến.")
    return out


def recommendations(results: list[dict]) -> list[str]:
    """Kiến nghị tổng hợp dựa trên toàn bộ kết quả."""
    rec: list[str] = []
    failed = [r for r in results if not r["evaluation"]["passed"]]
    slow = [r for r in failed if not r["evaluation"]["p95_ok"]]
    err = [r for r in failed if not r["evaluation"]["err_ok"]]
    if not failed:
        rec.append("Tất cả kịch bản đạt ngưỡng đánh giá; có thể nâng mức tải (Stress Test) để xác định giới hạn hệ thống.")
    if slow:
        names = ", ".join(sorted({r["uc_code"] for r in slow}))
        rec.append(f"Rà soát truy vấn cơ sở dữ liệu, chỉ mục (index), cơ chế cache và phân trang cho các UC vượt ngưỡng thời gian phản hồi: {names}.")
    def _non_429(r: dict) -> int:
        return sum(v for k, v in (r["summary"].get("status_codes") or {}).items() if not str(k).startswith("429"))

    err_real = [r for r in err if _non_429(r) > r["summary"].get("samples", 0) * 0.001]
    if err_real:
        names = ", ".join(sorted({r["uc_code"] for r in err_real}))
        rec.append(f"Phân tích log máy chủ (HTTP 5xx/timeout) và cấu hình giới hạn kết nối, thread pool, timeout của gateway cho: {names}.")
    rl = sorted({r["uc_code"] for r in results
                 if any(str(k).startswith("429") for k in (r["summary"].get("status_codes") or {}))})
    if rl:
        rec.append(f"Phát hiện giới hạn tần suất (HTTP 429) ở {', '.join(rl)}: cấu hình danh sách tài khoản kiểm thử "
                   "(mỗi người dùng ảo 1 tài khoản) hoặc đề nghị nới rate limit cho IP máy kiểm thử trước khi tăng tải.")
    if any(r["summary"].get("max_vus", 0) and r["summary"].get("error_rate", 0) > 0.5 for r in results):
        rec.append("Tỷ lệ lỗi rất cao – kiểm tra cơ chế rate-limit/WAF hoặc phiên đăng nhập trước khi đánh giá hiệu năng.")
    incons = sorted({f"{r['uc_code']} ({r['tool']})" for r in results if r["scenario_type"] == "smoke"
                     and not r["evaluation"]["passed"]
                     and any(o["uc_code"] == r["uc_code"] and o["tool"] == r["tool"] and o["scenario_type"] != "smoke"
                             and o["evaluation"]["passed"] for o in results)})
    if incons:
        rec.append(f"Smoke Test không đạt nhưng kịch bản tải lớn hơn lại đạt ở {', '.join(incons)}: tìm nguyên nhân "
                   "(mẫu nhỏ, khởi động nguội, sự cố tạm thời, lỗi kịch bản) và chạy lại Smoke Test trước khi dùng kết quả "
                   "các kịch bản sau làm căn cứ nghiệm thu.")
    rec.append("Thu thập thêm số liệu tài nguyên máy chủ (CPU, RAM, I/O, kết nối DB) trong lần kiểm thử tiếp theo để xác định điểm nghẽn.")
    return rec


def overview_insights(rows: list[dict]) -> list[str]:
    """Nhận xét so sánh giữa các kịch bản (loại kịch bản, công cụ, UC) dựa trên số liệu thực tế."""
    out: list[str] = []
    if len(rows) < 2:
        return out
    order = ["smoke", "load", "stress", "spike", "soak"]
    types = sorted({r["scenario_type"] for r in rows}, key=lambda t: order.index(t) if t in order else 9)
    tools = sorted({r["tool"] for r in rows})
    ucs = sorted({r["uc_code"] for r in rows})
    total = sum(r["summary"].get("samples", 0) for r in rows)
    total_txt = f"{total:,}".replace(",", ".")
    # 1 lượt chạy (run_id) có thể chứa nhiều UC (chế độ gộp) -> đếm riêng số lượt và số kết quả (lượt × UC)
    n_runs = len({r.get("run_id") for r in rows if r.get("run_id")}) or len(rows)
    out.append(f"Đã thực hiện {n_runs} lượt kiểm thử, thu được {len(rows)} kết quả (lượt × UC): {len(ucs)} use case, "
               f"{len(types)} loại kịch bản ({', '.join(scenario_name(t) for t in types)}), {len(tools)} công cụ; "
               f"tổng cộng {total_txt} request.")

    by = {(r["uc_code"], r["scenario_type"], r["tool"]): r for r in rows}

    # tải nhẹ -> tải nặng: p95 và thông lượng trên mỗi VU
    if len(types) >= 2:
        lo_t, hi_t = types[0], types[-1]
        parts = []
        for uc in ucs:
            for tool in tools:
                a, b = by.get((uc, lo_t, tool)), by.get((uc, hi_t, tool))
                if not a or not b or not a["summary"].get("p95") or not b["summary"].get("p95"):
                    continue
                ratio = b["summary"]["p95"] / a["summary"]["p95"]
                parts.append((uc, tool, a, b, ratio))
        if parts:
            worst = max(parts, key=lambda x: x[4])
            best = min(parts, key=lambda x: x[4])
            out.append(f"Khi tăng từ {scenario_name(lo_t)} lên {scenario_name(hi_t)}, p95 thay đổi từ ×{best[4]:.1f} ({best[0]}, {best[1]}) "
                       f"đến ×{worst[4]:.1f} ({worst[0]}, {worst[1]}: {worst[2]['summary']['p95']:.0f} → "
                       f"{worst[3]['summary']['p95']:.0f} ms). "
                       + ("Thời gian phản hồi gần như không đổi khi tăng tải – hệ thống còn dư năng lực."
                          if worst[4] < 1.5 else
                          f"{worst[0]} nhạy cảm nhất với tải, cần ưu tiên theo dõi khi tăng quy mô."))
            eff = []
            for uc, tool, a, b, _ in parts:
                va, vb = a["summary"].get("max_vus") or 1, b["summary"].get("max_vus") or 1
                if vb > va:
                    scale = (b["summary"]["throughput"] / a["summary"]["throughput"]) / (vb / va)
                    eff.append((uc, tool, scale))
            if eff:
                avg = sum(e[2] for e in eff) / len(eff)
                out.append(f"Thông lượng tăng tương ứng {avg * 100:.0f}% so với mức tăng số người dùng "
                           f"({scenario_name(lo_t)} → {scenario_name(hi_t)}); "
                           + ("tăng gần tuyến tính – chưa có dấu hiệu bão hoà."
                              if avg >= 0.8 else "tăng chậm hơn số người dùng – bắt đầu có dấu hiệu bão hoà."))

    # so sánh công cụ
    if len(tools) >= 2:
        diffs = []
        for uc in ucs:
            for typ in types:
                a, b = by.get((uc, typ, "k6")), by.get((uc, typ, "jmeter"))
                if a and b and a["summary"].get("p95") and b["summary"].get("p95"):
                    diffs.append(abs(a["summary"]["p95"] - b["summary"]["p95"]) / max(a["summary"]["p95"], 1))
        if diffs:
            m = sum(diffs) / len(diffs)
            out.append(f"Kết quả giữa k6 và JMeter chênh lệch trung bình {m * 100:.0f}% về p95 trên {len(diffs)} cặp "
                       "kịch bản – " + ("hai công cụ cho kết quả nhất quán, số liệu đáng tin cậy." if m <= 0.3 else
                                        "chênh lệch đáng kể, nên kiểm tra lại cấu hình think time/pacing giữa hai công cụ."))

    # lỗi theo loại kịch bản
    err_rows = [r for r in rows if r["summary"].get("errors")]
    if err_rows:
        codes: dict[str, int] = {}
        for r in err_rows:
            for k, v in (r["summary"].get("status_codes") or {}).items():
                codes[str(k)] = codes.get(str(k), 0) + v
        top = ", ".join(f"{k}: {v}" for k, v in sorted(codes.items(), key=lambda kv: -kv[1])[:3])
        where = ", ".join(sorted({f"{r['uc_code']}/{scenario_name(r['scenario_type'])}" for r in err_rows}))
        out.append(f"Lỗi xuất hiện ở {len(err_rows)}/{len(rows)} lượt ({where}); mã lỗi chủ yếu: {top}.")
    else:
        out.append("Không ghi nhận lỗi ở bất kỳ lượt kiểm thử nào.")

    out.extend(smoke_inconsistencies(rows))
    slow = max(rows, key=lambda r: (r["summary"].get("p95") or 0) / max(r["p95_threshold"], 1))
    out.append(f"Kịch bản chịu áp lực cao nhất: {slow['uc_code']} ({slow['tool']}, {scenario_name(slow['scenario_type'])}) với p95 "
               f"{slow['summary'].get('p95', 0):.0f} ms, {pct_of_thr(slow)}.")
    return out


def _early_share(row: dict, col: str, frac: float = 0.2) -> Optional[float]:
    """Tỷ lệ (lỗi) rơi vào 20% đầu phiên – dấu hiệu khởi động nguội nếu cao."""
    try:
        ts = pd.read_csv(row.get("ts_file", ""))
    except Exception:  # noqa: BLE001
        return None
    if ts.empty or col not in ts or ts[col].sum() <= 0:
        return None
    cut = ts["t"].max() * frac
    return float(ts.loc[ts["t"] <= cut, col].sum() / ts[col].sum())


def smoke_inconsistencies(rows: list[dict]) -> list[str]:
    """Smoke Test KHÔNG đạt nhưng kịch bản tải nặng hơn (cùng UC, cùng công cụ) lại đạt -> phân tích nguyên nhân khả dĩ."""
    out: list[str] = []
    by: dict[tuple, dict[str, dict]] = {}
    for r in rows:
        by.setdefault((r["uc_code"], r["tool"]), {})[r["scenario_type"]] = r
    for (uc, tool), group in sorted(by.items()):
        smoke = group.get("smoke")
        heavier = [group[t] for t in ("load", "stress", "spike", "soak") if t in group and group[t]["evaluation"]["passed"]]
        if not smoke or smoke["evaluation"]["passed"] or not heavier:
            continue
        s, ev = smoke["summary"], smoke["evaluation"]
        n, errs = s.get("samples", 0), s.get("errors", 0)
        causes = []
        if not ev["err_ok"]:
            codes = {str(k): v for k, v in (s.get("status_codes") or {}).items()}
            if n < 1000 or errs <= 10:
                causes.append(f"mẫu nhỏ – chỉ {errs} lỗi trên {n} request đã vượt ngưỡng tỷ lệ lỗi, trong khi cùng số lỗi đó "
                              "ở kịch bản lớn chỉ là tỷ lệ rất nhỏ")
            early = _early_share(smoke, "errors")
            if early is not None and early >= 0.6:
                causes.append(f"{early:.0%} số lỗi dồn vào 20% đầu phiên – dấu hiệu khởi động nguội (cache, kết nối CSDL, "
                              "phiên đăng nhập chưa sẵn sàng)")
            if any(k.startswith("429") for k in codes):
                causes.append("có lỗi HTTP 429 – giới hạn tần suất tính theo cửa sổ thời gian nên phụ thuộc thời điểm chạy")
        if not ev["p95_ok"]:
            if n < 1000:
                causes.append(f"p95 tính trên ít mẫu ({n} request) nên vài request chậm đã đẩy p95 vượt ngưỡng")
            try:
                ts = pd.read_csv(smoke.get("ts_file", ""))
                cut = ts["t"].max() * 0.2
                if not ts.empty and ts.loc[ts["t"] <= cut, "p95"].max() > 1.5 * ts.loc[ts["t"] > cut, "p95"].median():
                    causes.append("thời gian phản hồi cao bất thường ở đầu phiên rồi giảm dần – dấu hiệu khởi động nguội")
            except Exception:  # noqa: BLE001
                pass
        if not causes:
            causes.append("không thấy dấu hiệu mẫu nhỏ hay khởi động nguội – khả năng sự cố tạm thời của môi trường hoặc "
                          "lỗi kịch bản; xem chi tiết lỗi theo request ở bước 8")
        passed_names = ", ".join(scenario_name(h["scenario_type"]) for h in heavier)
        out.append(f"{uc} ({tool}): Smoke Test KHÔNG đạt ({'; '.join(ev['reasons'])}) nhưng {passed_names} đạt. "
                   f"Nguyên nhân khả dĩ: {'; '.join(causes)}. Cần xử lý nguyên nhân và chạy lại Smoke Test trước khi "
                   f"kết luận dựa trên {passed_names}.")
    return out


def module_summaries(rows: list[dict]) -> list[dict]:
    """Tổng hợp kết quả theo phân hệ (theo cột Phân hệ của file UC) – dùng cho báo cáo & giao diện."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("module") or "Chưa phân loại", []).append(r)
    out = []
    for mod, g in groups.items():
        passed = [r for r in g if r["evaluation"]["passed"]]
        worst = max(g, key=lambda r: (r["summary"].get("p95") or 0) / max(r["p95_threshold"], 1))
        n_req = sum(r["summary"].get("samples", 0) for r in g)
        n_err = sum(r["summary"].get("errors", 0) for r in g)
        tps = [r["summary"].get("throughput") or 0 for r in g]
        ucs = sorted({r["uc_code"] for r in g})
        if len(passed) == len(g):
            verdict = "Đạt"
        elif not passed:
            verdict = "Không đạt"
        else:
            verdict = "Đạt một phần"
        failed_ucs = sorted({r["uc_code"] for r in g if not r["evaluation"]["passed"]})
        slow = sorted({r["uc_code"] for r in g if not r["evaluation"]["p95_ok"]})
        err_ucs = sorted({r["uc_code"] for r in g if not r["evaluation"]["err_ok"]})
        codes: dict[str, int] = {}
        for r in g:
            for k, v in (r["summary"].get("status_codes") or {}).items():
                codes[str(k)] = codes.get(str(k), 0) + v
        types = sorted({r["scenario_type"] for r in g}, key=lambda t: ["smoke", "load", "stress", "spike", "soak"].index(t)
                       if t in ("smoke", "load", "stress", "spike", "soak") else 9)
        text = (f"Phân hệ {mod}: kiểm thử {len(ucs)} UC ({', '.join(ucs)}) qua {len(g)} kịch bản "
                f"({', '.join(scenario_name(t) for t in types)}) – {len(passed)}/{len(g)} kịch bản đạt. "
                f"p95 cao nhất {worst['summary'].get('p95', 0):.0f} ms ({worst['uc_code']}, {worst['tool']}, "
                f"{scenario_name(worst['scenario_type'])}), {pct_of_thr(worst)}.")
        if failed_ucs:
            reasons = []
            if slow:
                reasons.append(f"thời gian phản hồi vượt ngưỡng ở {', '.join(slow)}")
            if err_ucs:
                top = ", ".join(f"{k}: {v}" for k, v in sorted(codes.items(), key=lambda kv: -kv[1])[:3])
                reasons.append(f"tỷ lệ lỗi vượt ngưỡng ở {', '.join(err_ucs)} (mã lỗi chủ yếu {top})")
            text += (" Không đạt do " if slow else " Thời gian phản hồi đạt, nhưng không đạt do ") + "; ".join(reasons) + "."
        else:
            text += " Tất cả kịch bản đạt ngưỡng đánh giá."
        out.append({"module": mod, "ucs": ucs, "runs": len(g), "passed": len(passed), "verdict": verdict,
                    "p95_max": worst["summary"].get("p95") or 0, "p95_worst_uc": worst["uc_code"],
                    "threshold": worst["p95_threshold"], "error_rate": n_err / n_req if n_req else 0.0,
                    "requests": n_req, "throughput": sum(tps) / len(tps) if tps else 0.0,
                    "failed_ucs": failed_ucs, "text": text})
    return out
