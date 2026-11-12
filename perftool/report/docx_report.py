"""Xuất báo cáo kết quả kiểm thử hiệu năng (.docx) theo cấu trúc & định dạng của file mẫu.

Cấu trúc:
  Tiêu đề + Bảng thông tin chung
  1. THÔNG TIN HỆ THỐNG, PHẠM VI VÀ CẤU HÌNH KIỂM THỬ
  2. BÁO CÁO CHI TIẾT KẾT QUẢ KIỂM THỬ HIỆU NĂNG
     Bảng tổng hợp kịch bản
     2.1. TỔNG QUAN CÁC KỊCH BẢN (khi có từ 2 kịch bản): bảng theo loại × công cụ, biểu đồ VUs/p95/thông lượng
          theo thời gian của tất cả kịch bản, nhận xét so sánh
     2.2. TỔNG HỢP KẾT QUẢ THEO PHÂN HỆ (khi có từ 2 phân hệ): bảng + biểu đồ p95 + nhận định từng phân hệ
     2.3. A. <Hệ thống>  > 2.3.x. <Phân hệ> (bảng tóm tắt phân hệ) > 2.3.x.y. <UC> — <Loại kịch bản> (<công cụ>)
        Bảng cấu hình kịch bản | Bảng chỉ số | Bảng theo request/API | Biểu đồ | Nhận định
  3. TỔNG HỢP VÀ KẾT LUẬN
File mẫu (config report.template) được dùng làm nền: giữ nguyên style, lề trang, header/footer.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .. import config
from ..charts.builder import CHART_TITLES, tool_name
from ..config import ROOT_DIR
from ..models import Project, TestConfig
from ..results.insights import module_summaries, overview_insights, pct_of_thr, recommendations
from ..results.metrics import fmt_ms, fmt_pct
from ..scriptgen.profile import scenario_name

HEADER_FILL = "D9E2F3"
GROUP_FILL = "F2F2F2"
BORDER = "808080"
FONT_PT = 12
IMG_WIDTH = Inches(6.22)


class ReportBuilder:
    def __init__(self, template: Optional[Path] = None):
        tpl = template or (ROOT_DIR / (config.get("report.template") or "Template_BaoCaoHieuNang.docx"))
        if tpl and Path(tpl).exists():
            self.doc = Document(str(tpl))
            self._clear_body()
        else:
            self.doc = Document()
            self.doc.styles["Normal"].font.name = "Times New Roman"
            self.doc.styles["Normal"].font.size = Pt(13)
        self.fig_no = 0

    # ------------------------------------------------------------ primitives
    def _clear_body(self) -> None:
        body = self.doc.element.body
        for el in list(body):
            if el.tag != qn("w:sectPr"):
                body.remove(el)

    def para(self, text: str = "", bold: bool = False, italic: bool = False, size: Optional[float] = None,
             align=None, after: Optional[int] = None, color: Optional[str] = None, style: Optional[str] = None):
        p = self.doc.add_paragraph(style=style)
        if text:
            r = p.add_run(text)
            r.bold, r.italic = bold, italic
            if size:
                r.font.size = Pt(size)
            if color:
                r.font.color.rgb = RGBColor.from_string(color)
        if align is not None:
            p.alignment = align
        if after is not None:
            p.paragraph_format.space_after = Pt(after / 20)
        return p

    def heading(self, text: str, level: int):
        try:
            return self.doc.add_heading(text, level=level)
        except KeyError:
            return self.para(text, bold=True, size={1: 14, 2: 13, 3: 13, 4: 12}.get(level, 12))

    def labeled(self, label: str, text: str, after: int = 160):
        p = self.doc.add_paragraph()
        r = p.add_run(label)
        r.bold = True
        r.font.size = Pt(FONT_PT)
        r2 = p.add_run(text)
        r2.font.size = Pt(FONT_PT)
        p.paragraph_format.space_after = Pt(after / 20)
        return p

    def bullet(self, text: str):
        return self.para(f"- {text}", after=60)

    def figure(self, path: Optional[str], caption: str) -> None:
        if not path or not Path(path).exists():
            return
        self.fig_no += 1
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(path, width=IMG_WIDTH)
        self.para(f"Hình {self.fig_no}. {caption}", italic=True, size=10, align=WD_ALIGN_PARAGRAPH.CENTER, after=160)

    def table(self, header: list[str], rows: list[list[str]], widths: list[int], title_row: Optional[str] = None,
              center_cols: tuple[int, ...] = (), header_first_col: bool = False):
        """Bảng theo định dạng mẫu: viền xám 808080, hàng tiêu đề nền D9E2F3 chữ đậm, cỡ 12pt."""
        n_rows = len(rows) + 1 + (1 if title_row else 0)
        t = self.doc.add_table(rows=n_rows, cols=len(header))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._borders(t)
        grid = t._tbl.tblGrid
        for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
            gc.set(qn("w:w"), str(w))
        r0 = 0
        if title_row:
            c = t.rows[0].cells[0].merge(t.rows[0].cells[-1])
            self._cell(c, title_row, bold=True, fill=HEADER_FILL, center=True, width=sum(widths))
            self._repeat_header(t.rows[0])
            r0 = 1
        for j, h in enumerate(header):
            self._cell(t.rows[r0].cells[j], h, bold=True, fill=HEADER_FILL, center=True, width=widths[j])
        self._repeat_header(t.rows[r0])
        for i, row in enumerate(rows, start=r0 + 1):
            if isinstance(row, dict):                     # dòng nhóm, vd {"group": "Phân hệ: ..."}
                c = t.rows[i].cells[0].merge(t.rows[i].cells[-1])
                self._cell(c, row["group"], bold=True, fill=GROUP_FILL, width=sum(widths))
                continue
            for j, v in enumerate(row):
                self._cell(t.rows[i].cells[j], str(v), center=j in center_cols, width=widths[j],
                           bold=header_first_col and j == 0)
        self.para(after=60)
        return t

    @staticmethod
    def _borders(t) -> None:
        tblPr = t._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{side}")
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), "6")
            e.set(qn("w:space"), "0")
            e.set(qn("w:color"), BORDER)
            b.append(e)
        tblPr.append(b)

    @staticmethod
    def _repeat_header(row) -> None:
        trPr = row._tr.get_or_add_trPr()
        el = OxmlElement("w:tblHeader")
        trPr.append(el)

    @staticmethod
    def _cell(cell, text: str, bold: bool = False, fill: Optional[str] = None, center: bool = False,
              width: Optional[int] = None, color: Optional[str] = None) -> None:
        tcPr = cell._tc.get_or_add_tcPr()
        if width:
            w = OxmlElement("w:tcW")
            w.set(qn("w:w"), str(width))
            w.set(qn("w:type"), "dxa")
            tcPr.append(w)
        if fill:
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), fill)
            tcPr.append(shd)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(1)
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(FONT_PT)
        if color:
            r.font.color.rgb = RGBColor.from_string(color)

    def save(self, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(out))
        return out


# ============================================================================ nội dung
def _accounts_text(rows: list[dict]) -> str:
    counts = sorted({int(r["config"].get("accounts_count") or 1) for r in rows}) or [1]
    txt = " / ".join(str(c) for c in counts)
    return f"{txt} tài khoản" + (" – mỗi người dùng ảo đăng nhập bằng 1 tài khoản, chia đều theo vòng"
                                  if counts[-1] > 1 else " – mọi người dùng ảo dùng chung")


def _run_values(rows: list[dict], cfg: TestConfig, key: str, fmt=str) -> str:
    """Giá trị cấu hình của CÁC LƯỢT đưa vào báo cáo (không phải cấu hình hiện tại ở 7a); nhiều giá trị -> 'a / b'."""
    vals: list[str] = []
    for r in rows:
        v = (r.get("config") or {}).get(key, getattr(cfg, key, ""))
        if key == "scenario_type":
            v = r.get("scenario_type") or v
        t = fmt(v)
        if t not in vals:
            vals.append(t)
    return " / ".join(vals) if vals else fmt(getattr(cfg, key, ""))


def _verdict(passed: bool) -> str:
    return "Đạt" if passed else "Không đạt"


def build_report(project: Project, rows: list[dict], charts: dict[str, dict[str, str]], comparison_png: str,
                 out_file: Path, options: Optional[dict] = None) -> Path:
    opt = {"extra_charts": True, "api_table": True, "insights": True, "resources": True, **(options or {})}
    opt["_mon"] = None
    if opt["resources"] and project.monitor.enabled:
        from ..monitor.store import load_all
        opt["_mon"] = load_all(project.id)
    rb = ReportBuilder()
    info, cfg = project.info, project.test_config
    passed = [r for r in rows if r["evaluation"]["passed"]]
    failed = [r for r in rows if not r["evaluation"]["passed"]]

    # ---------------------------------------------------------------- tiêu đề
    rb.para("BÁO CÁO KẾT QUẢ KIỂM THỬ HIỆU NĂNG", bold=True, size=17, align=WD_ALIGN_PARAGRAPH.CENTER)
    rb.para(f"Dự án “{info.name}”", bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
    rb.para()
    tools = sorted({tool_name(r["tool"]) for r in rows}) or [tool_name(t) for t in cfg.tools]
    ucs = OrderedDict((r["uc_code"], r) for r in rows)
    rb.table(["Nội dung", "Thông tin"], [
        ["Dự án", info.name],
        ["Hệ thống", info.system_name or project.login.base_url],
        ["Địa chỉ hệ thống (URL)", project.login.base_url],
        ["Môi trường kiểm thử", info.environment],
        ["Ngày lập báo cáo", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["Người thực hiện", info.author or "—"],
        ["Phạm vi", f"{len(project.use_cases)} use case trong danh sách / {len(ucs)} use case được kiểm thử hiệu năng"],
        ["Công cụ", ", ".join(tools)],
        ["Kiểm thử hiệu năng", f"{len(passed)} kịch bản đạt / {len(failed)} kịch bản không đạt"],
    ], widths=[2608, 7030])

    # ---------------------------------------------------------------- 1. phạm vi & cấu hình
    rb.heading("1.\tTHÔNG TIN HỆ THỐNG, PHẠM VI VÀ CẤU HÌNH KIỂM THỬ", 1)
    rb.heading("1.1.\tThông tin hệ thống", 2)
    mods = [m for m in project.modules if m.enabled]
    n_pages = sum(len(m.pages) for m in mods)
    n_api = len({r.url.split("?")[0] for m in mods for p in m.pages for r in p.requests if r.resource_type in ("xhr", "fetch")})
    rb.table(["STT", "Thông số", "Giá trị"], [
        ["1", "Tên hệ thống", info.system_name or info.name],
        ["2", "URL", project.login.base_url],
        ["3", "Môi trường", info.environment],
        ["4", "Số phân hệ đã phân tích", str(len(mods))],
        ["5", "Số màn hình đã quét", str(n_pages)],
        ["6", "Số API (endpoint) ghi nhận", str(n_api)],
        ["7", "Cơ chế xác thực trong script", {"login_per_vu": "Mỗi người dùng ảo đăng nhập 1 lần",
                                               "static_headers": "Dùng cookie/token phiên đã ghi nhận",
                                               "none": "Không xác thực"}.get(cfg.auth_mode, cfg.auth_mode)],
    ], widths=[737, 2948, 5386], center_cols=(0,))

    rb.heading("1.2.\tPhạm vi kiểm thử", 2)
    rb.para("Các use case được lựa chọn kiểm thử hiệu năng dựa trên phân tích độ phức tạp (số bước, số màn hình, "
            "số API/request, thao tác CRUD, xử lý dữ liệu, thời gian phản hồi thực tế) và đã được xác nhận:",
            after=120)
    scores = {s.uc_code: s for s in project.scores}
    scope_rows = []
    for i, sc in enumerate(project.scenarios, 1):
        s = scores.get(sc.uc_code)
        scope_rows.append([str(i), sc.uc_code, sc.uc_name, sc.module or "—", f"{s.total:.1f}" if s else "—",
                           str(len([x for x in sc.steps if x.enabled])), (s.reason if s else "")[:180]])
    rb.table(["STT", "Mã UC", "Tên Use case", "Phân hệ", "Điểm", "Số request", "Lý do lựa chọn"], scope_rows,
             widths=[567, 1300, 2100, 1500, 750, 850, 2600], center_cols=(0, 4, 5))
    if project.score_caps:
        from ..analysis.complexity import CRITERIA, CRITERIA_UNIT, default_weights
        w = project.weights or default_weights()     # dự án chưa lưu trọng số -> trọng số mặc định đã dùng khi chấm điểm
        wsum = sum(w.values()) or 1
        rb.para("Tiêu chí chấm điểm độ phức tạp (Điểm = 100 × Σ điểm tiêu chí × trọng số; điểm tiêu chí = số liệu thô ÷ "
                "mức trần, tối đa 1):", after=60)
        rb.table(["STT", "Tiêu chí", "Trọng số", "Mức trần"],
                 [[str(i), label, f"{w.get(k, 0) / wsum:.0%}",
                   f"{project.score_caps.get(k, 0):g} {CRITERIA_UNIT[k]}"] for i, (k, label) in enumerate(CRITERIA.items(), 1)],
                 widths=[737, 4000, 2000, 2900], center_cols=(0, 2, 3))
        rb.para("Mức trần " + ("tự động theo dữ liệu của dự án (p90 của danh sách UC)." if project.caps_mode == "auto"
                               else "cố định theo cấu hình."), italic=True, size=10, after=120)

    rb.heading("1.3.\tCấu hình và tiêu chí đánh giá", 2)
    # cấu hình lấy từ các lượt chạy đưa vào báo cáo (cấu hình 7a có thể đã đổi sau khi chạy)
    rv = lambda key, fmt=str: _run_values(rows, cfg, key, fmt)  # noqa: E731
    p95s = {float((r.get("config") or {}).get("p95_threshold_ms", cfg.p95_threshold_ms)) for r in rows} \
        or {float(cfg.p95_threshold_ms)}
    rb.table(["STT", "Thông số", "Giá trị"], [
        ["1", "Công cụ", ", ".join(tools)],
        ["2", "Loại kịch bản", rv("scenario_type", scenario_name)],
        ["3", "Số người dùng ảo (VUs)", rv("vus")],
        ["4", "Thời gian tăng tải (ramp-up)", rv("ramp_up")],
        ["5", "Thời lượng giữ tải", rv("duration")],
        ["6", "Thời gian giảm tải (ramp-down)", rv("ramp_down")],
        ["7", "Thời gian nghỉ giữa thao tác (think time)", rv("think_time_s", lambda v: f"{v} giây")],
        ["8", "Ngưỡng p95 chung", rv("p95_threshold_ms", lambda v: f"{float(v):.0f} ms")
         + (" (mặc định của công cụ, chưa có SLA riêng)" if p95s == {TestConfig().p95_threshold_ms} else "")],
        ["9", "Ngưỡng tỷ lệ lỗi", rv("error_rate_threshold", lambda v: fmt_pct(float(v)))],
        ["10", "Chế độ chạy", rv("run_mode", lambda v: "Tuần tự từng UC" if v == "sequential"
                                  else "Gộp các UC chạy đồng thời")],
        ["11", "Số tài khoản kiểm thử", _accounts_text(rows)],
        ["12", "Số liệu tài nguyên máy chủ", _monitor_text(project, opt)],
    ], widths=[737, 3948, 4386], center_cols=(0,))

    # ---------------------------------------------------------------- 2. chi tiết
    rb.heading("2.\tBÁO CÁO CHI TIẾT KẾT QUẢ KIỂM THỬ HIỆU NĂNG", 1)
    by_module: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in rows:
        by_module.setdefault(r.get("module") or "Chưa phân loại", []).append(r)
    multi_mod = len(by_module) > 1
    rb.para("Bảng tổng hợp các kịch bản đã thực hiện" + (" (nhóm theo phân hệ):" if multi_mod else ":"), after=120)
    summary_rows: list = []
    for mi, (mod, mrows) in enumerate(by_module.items(), 1):
        if multi_mod:
            ok = sum(1 for r in mrows if r["evaluation"]["passed"])
            summary_rows.append({"group": f"{mi}. {mod} – {ok}/{len(mrows)} kịch bản đạt"})
        for r in mrows:
            s = r["summary"]
            summary_rows.append([r["uc_code"], r["uc_name"], r["tool"], scenario_name(r["scenario_type"]),
                                 str(s.get("max_vus") or r["config"].get("vus", "")), f"{s.get('p95', 0):.1f}",
                                 f"{r['p95_threshold']:.0f}", fmt_pct(s.get("error_rate")) if s.get("errors") else "-",
                                 _verdict(r["evaluation"]["passed"])])
    rb.table(["Mã UC", "Tên Use case", "Công cụ", "Kịch bản", "Số NDĐT", "p95 (ms)", "Ngưỡng (ms)", "Tỷ lệ lỗi",
              "Kết luận"], summary_rows, widths=[1304, 2041, 907, 907, 897, 996, 1040, 1076, 964],
             center_cols=(2, 3, 4, 5, 6, 7, 8))
    overview = opt.get("overview", True) and len(rows) > 1
    if len(rows) > 1 and not overview:
        rb.figure(comparison_png, "So sánh thời gian phản hồi p95 giữa các kịch bản và ngưỡng đánh giá")

    sec = 1
    if overview:
        _overview_section(rb, rows, opt.get("overview_charts") or {}, f"2.{sec}.")
        sec += 1
    mod_sums = {m["module"]: m for m in module_summaries(rows)}
    if opt.get("modules", True) and multi_mod:
        _module_section(rb, list(mod_sums.values()), opt.get("overview_charts") or {}, f"2.{sec}.")
        sec += 1

    # chỉ 1 hệ đánh số (trước đây thành "2.3. A. …" và "2.3.1. 1. …")
    rb.heading(f"2.{sec}.\t{(info.system_name or info.name).upper()}", 2)
    for mi, (mod, mrows) in enumerate(by_module.items(), 1):
        rb.heading(f"2.{sec}.{mi}.\t{mod}", 3)
        ms = mod_sums.get(mod)
        if ms and len(mrows) > 1:
            rb.para(f"Tóm tắt phân hệ – kết luận: {ms['verdict']} ({ms['passed']}/{ms['runs']} kịch bản đạt).",
                    bold=True, after=60)
            rb.table(["STT", "Mã UC", "Tên Use case", "Công cụ", "Kịch bản", "p95 (ms)", "Tỷ lệ lỗi", "Kết luận"],
                     [[str(i), r["uc_code"], r["uc_name"], r["tool"], scenario_name(r["scenario_type"]),
                       f"{r['summary'].get('p95', 0):.0f}", fmt_pct(r["summary"].get("error_rate")),
                       _verdict(r["evaluation"]["passed"])] for i, r in enumerate(mrows, 1)],
                     widths=[567, 1250, 2700, 950, 1150, 950, 950, 1100], center_cols=(0, 3, 4, 5, 6, 7))
        for ri, r in enumerate(mrows, 1):
            _uc_section(rb, project, r, charts.get(f"{r['run_id']}|{r['uc_code']}", {}), f"2.{sec}.{mi}.{ri}.", opt)

    # ---------------------------------------------------------------- 3. kết luận
    rb.heading("3.\tTỔNG HỢP VÀ KẾT LUẬN", 1)
    rb.bullet(f"Đã thực hiện {len(rows)} kịch bản kiểm thử hiệu năng cho {len(ucs)} use case bằng {', '.join(tools)}.")
    rb.bullet(f"Có {len(passed)} kịch bản đạt và {len(failed)} kịch bản không đạt ngưỡng đánh giá.")
    slow = [r for r in failed if not r["evaluation"]["p95_ok"]]
    err_only = [r for r in failed if r["evaluation"]["p95_ok"] and not r["evaluation"]["err_ok"]]
    if slow:
        rb.bullet(f"Có {len(slow)} kịch bản có thời gian phản hồi p95 vượt ngưỡng đánh giá, cần rà soát truy vấn và cấu hình "
                  "máy chủ trước khi đưa vào vận hành.")
    if err_only:
        rb.bullet(f"Có {len(err_only)} kịch bản đạt ngưỡng thời gian phản hồi nhưng không đạt do tỷ lệ lỗi vượt ngưỡng "
                  f"({', '.join(sorted({r['uc_code'] for r in err_only}))}).")
    if rows and not slow:
        mx = max(rows, key=lambda r: (r["summary"].get("p95") or 0) / max(r["p95_threshold"], 1))
        rb.bullet(f"Tất cả kịch bản đều có p95 dưới ngưỡng đánh giá; cao nhất là {mx['uc_code']} ({mx['tool']}, "
                  f"{scenario_name(mx['scenario_type'])}) với {fmt_ms(mx['summary'].get('p95'))} "
                  f"(≈{pct_of_thr(mx).removeprefix('bằng ')}).")
    if rows:
        worst = max(rows, key=lambda r: r["summary"].get("p95") or 0)
        best_tp = max(rows, key=lambda r: r["summary"].get("throughput") or 0)
        if slow:
            rb.bullet(f"Kịch bản có p95 cao nhất: {worst['uc_code']} ({worst['tool']}, {scenario_name(worst['scenario_type'])}) – "
                      f"{fmt_ms(worst['summary'].get('p95'))}.")
        rb.bullet(f"Thông lượng cao nhất đạt {best_tp['summary'].get('throughput', 0):.2f} req/s "
                  f"({best_tp['uc_code']}, {best_tp['tool']}).")
    if failed:
        rb.para("Danh sách kịch bản không đạt:", after=120)
        rb.table(["Mã UC", "Use case", "Công cụ / Kịch bản", "Nội dung không đạt"],
                 [[r["uc_code"], r["uc_name"], f"{r['tool']} / {scenario_name(r['scenario_type'])}",
                   "; ".join(r["evaluation"]["reasons"])] for r in failed],
                 widths=[1500, 3000, 1600, 3538])
    if multi_mod:
        rb.para("Kết luận theo phân hệ:", bold=True, after=60)
        for m in mod_sums.values():
            rb.bullet(f"{m['module']}: {m['verdict']} ({m['passed']}/{m['runs']} kịch bản đạt; UC {', '.join(m['ucs'])}; "
                      f"p95 cao nhất {m['p95_max']:.0f} ms)"
                      + (f" – cần xử lý {', '.join(m['failed_ucs'])}." if m["failed_ucs"] else "."))
    rb.para("Kiến nghị:", bold=True, after=60)
    for rec in _resource_recs(rows, opt) or recommendations(rows):
        rb.bullet(rec)
    if project.conclusions.strip():
        rb.para("Nhận xét bổ sung:", bold=True, after=60)
        for line in project.conclusions.strip().splitlines():
            if line.strip():
                rb.bullet(line.strip().lstrip("-• "))
    return rb.save(out_file)


SCENARIO_ORDER = ["smoke", "load", "stress", "spike", "soak"]


def _duration_text(typ: str, c: dict) -> str:
    """Thời lượng đúng theo hồ sơ tải: Stress/Spike chạy theo bậc nên tổng thời gian khác ramp + giữ + giảm."""
    if typ == "smoke":
        return str(c.get("duration"))
    if typ in ("stress", "spike"):
        try:
            from ..models import TestConfig
            from ..scriptgen.profile import fmt_duration, total_seconds
            return f"{fmt_duration(total_seconds(TestConfig.model_validate(c)))} (theo bậc)"
        except Exception:  # noqa: BLE001
            pass
    return f"{c.get('ramp_up')} + {c.get('duration')} + {c.get('ramp_down')}"


def _overview_section(rb: ReportBuilder, rows: list[dict], ch: dict[str, str], num: str) -> None:
    """Mục tổng quan: bảng theo loại kịch bản × công cụ, biểu đồ tổng quát và nhận xét so sánh."""
    rb.heading(f"{num}\tTỔNG QUAN CÁC KỊCH BẢN KIỂM THỬ", 2)
    groups: "OrderedDict[tuple, list[dict]]" = OrderedDict()
    for r in sorted(rows, key=lambda r: (SCENARIO_ORDER.index(r["scenario_type"])
                                         if r["scenario_type"] in SCENARIO_ORDER else 9, r["tool"])):
        groups.setdefault((r["scenario_type"], r["tool"]), []).append(r)
    trows = []
    for i, ((typ, tool), g) in enumerate(groups.items(), 1):
        c = g[0]["config"]
        dur = _duration_text(typ, c)
        n = sum(r["summary"].get("samples", 0) for r in g)
        errs = sum(r["summary"].get("errors", 0) for r in g)
        p95s = [r["summary"].get("p95") or 0 for r in g]
        tps = [r["summary"].get("throughput") or 0 for r in g]
        passed = sum(1 for r in g if r["evaluation"]["passed"])
        trows.append([str(i), scenario_name(typ), tool_name(tool), str(len(g)), str(max(r["summary"].get("max_vus") or 0 for r in g)),
                      dur, f"{n:,}".replace(",", "."), f"{sum(tps) / len(tps):.1f}",
                      f"{min(p95s):.0f}–{max(p95s):.0f}", fmt_pct(errs / n if n else 0), f"{passed}/{len(g)}"])
    rb.para("Bảng tổng hợp theo loại kịch bản và công cụ:", after=60)
    rb.table(["STT", "Kịch bản", "Công cụ", "Số UC", "VUs", "Thời lượng", "Tổng request", "Thông lượng TB (req/s)",
              "p95 (ms)", "Tỷ lệ lỗi", "Đạt"], trows,
             widths=[500, 850, 1050, 650, 650, 1450, 1000, 1000, 950, 850, 600],
             center_cols=tuple(range(11)))
    captions = [("vus", "Số người dùng đồng thời theo thời gian – tổng quan các kịch bản (đo thực tế)"),
                ("p95", "Thời gian phản hồi p95 theo thời gian – so sánh các kịch bản"),
                ("comparison", "So sánh thời gian phản hồi p95 giữa các kịch bản và ngưỡng đánh giá"),
                ("throughput", "Thông lượng trung bình theo kịch bản")]
    for key, cap in captions:
        rb.figure(ch.get(key), cap)
    notes = overview_insights(rows)
    if notes:
        rb.para("Nhận xét tổng quan:", bold=True, after=60)
        for n in notes:
            rb.bullet(n)


def _module_section(rb: ReportBuilder, sums: list[dict], ch: dict[str, str], num: str) -> None:
    """Mục tổng hợp kết quả theo phân hệ: bảng + biểu đồ + nhận định từng phân hệ."""
    rb.heading(f"{num}\tTỔNG HỢP KẾT QUẢ THEO PHÂN HỆ", 2)
    rb.para(f"Kết quả được tổng hợp cho {len(sums)} phân hệ (theo cột Phân hệ của danh sách Use Case):", after=60)
    rb.table(["STT", "Phân hệ", "Số UC", "Số kịch bản", "Đạt", "p95 cao nhất (ms)", "Tỷ lệ lỗi", "Thông lượng TB (req/s)",
              "Kết luận"],
             [[str(i), m["module"], str(len(m["ucs"])), str(m["runs"]), f"{m['passed']}/{m['runs']}",
               f"{m['p95_max']:.0f} ({m['p95_worst_uc']})", fmt_pct(m["error_rate"]), f"{m['throughput']:.1f}",
               m["verdict"]] for i, m in enumerate(sums, 1)],
             widths=[500, 2500, 650, 800, 650, 1300, 900, 1100, 1100], center_cols=(0, 2, 3, 4, 5, 6, 7, 8))
    rb.figure(ch.get("modules"), "Thời gian phản hồi p95 cao nhất theo phân hệ so với ngưỡng đánh giá")
    rb.para("Nhận định theo phân hệ:", bold=True, after=60)
    for m in sums:
        rb.bullet(m["text"])


def _uc_section(rb: ReportBuilder, project: Project, r: dict, ch: dict[str, str], num: str, opt: dict) -> None:
    s, ev, c = r["summary"], r["evaluation"], r["config"]
    rb.heading(f"{num}\t{r['uc_name']} — {scenario_name(r['scenario_type'])} ({tool_name(r['tool'])})", 4)
    n_acc = int(c.get("accounts_count") or 1)
    cfg_rows = [["Công cụ", tool_name(r["tool"])], ["Kịch bản", scenario_name(r["scenario_type"])],
                ["Số người dùng ảo", f"{s['uc_vus']} (lượt chạy gộp: tổng {c.get('vus', '')} VU chia cho các UC)"
                 if s.get("uc_vus") else str(c.get("vus", ""))],
                ["Số tài khoản kiểm thử", f"{n_acc} (chia đều cho các người dùng ảo)" if n_acc > 1
                 else "1 (mọi người dùng ảo dùng chung)"],
                ["Thời lượng bắn tải", f"{c.get('ramp_up')} tăng tải + {c.get('duration')} giữ tải + {c.get('ramp_down')} giảm tải"
                 if r["scenario_type"] != "smoke" else str(c.get("duration"))],
                ["Tổng số mẫu ghi nhận", str(s.get("samples", 0))],
                ["Môi trường", project.info.environment],
                ["Mã lượt chạy", r["run_id"]],
                ["Dữ liệu thô", _rel(r.get("raw_file", ""))],
                ["Số liệu tài nguyên máy chủ", "có (xem mục Tài nguyên máy chủ bên dưới)" if _mon_slice(r, opt) is not None
                 else "không thu thập"]]
    rb.table(["STT", "Thông số", "Giá trị"], [[str(i), a, b] for i, (a, b) in enumerate(cfg_rows, 1)],
             widths=[737, 2948, 5386], title_row="Cấu hình kịch bản", center_cols=(0,))
    thr, er_thr = r["p95_threshold"], r["error_threshold"]
    metric_rows = [
        ["Thời gian phản hồi trung bình", fmt_ms(s.get("avg")), "—", "—"],
        ["Phân vị 50 (p50)", fmt_ms(s.get("p50")), "—", "—"],
        ["Phân vị 90 (p90)", fmt_ms(s.get("p90")), "—", "—"],
        ["Phân vị 95 (p95)", fmt_ms(s.get("p95")), f"{thr:.0f} ms", _verdict(ev["p95_ok"])],
        ["Phân vị 99 (p99)", fmt_ms(s.get("p99")), "—", "—"],
        ["Thời gian phản hồi nhỏ nhất", fmt_ms(s.get("min")), "—", "—"],
        ["Thời gian phản hồi lớn nhất", fmt_ms(s.get("max")), "—", "—"],
        ["Thông lượng", f"{s.get('throughput', 0):.2f} req/s", "—", "—"],
        ["Tổng số request", str(s.get("samples", 0)), "—", "—"],
        ["Số người dùng đồng thời tối đa" + (" (cả lượt gộp)" if s.get("uc_vus") else ""), str(s.get("max_vus", 0)),
         "—", "—"],
        ["Tỷ lệ lỗi", fmt_pct(s.get("error_rate")), fmt_pct(er_thr), _verdict(ev["err_ok"])],
    ]
    rb.table(["STT", "Chỉ số", "Giá trị đo được", "Ngưỡng đánh giá", "Đánh giá"],
             [[str(i), *row] for i, row in enumerate(metric_rows, 1)],
             widths=[680, 3402, 1928, 1928, 1134], center_cols=(0, 2, 3, 4))
    labels = r.get("labels") or []
    if opt.get("api_table") and labels:
        rb.para("Kết quả theo từng request/API:", after=60)
        rb.table(["STT", "Request/API", "Số mẫu", "TB (ms)", "p90 (ms)", "p95 (ms)", "Max (ms)", "Lỗi"],
                 [[str(i), l["label"].split(" :: ", 1)[-1], str(l["samples"]), f"{l['avg']:.0f}", f"{l['p90']:.0f}",
                   f"{l['p95']:.0f}", f"{l['max']:.0f}", fmt_pct(l["error_rate"])] for i, l in enumerate(labels, 1)],
                 widths=[567, 3500, 850, 850, 850, 850, 850, 800], center_cols=(0, 2, 3, 4, 5, 6, 7))
    code = r["uc_code"]
    rb.figure(ch.get("percentile"), f"{CHART_TITLES['percentile']} — {code}")
    if r["scenario_type"] != "smoke":
        rb.figure(ch.get("timeline"), f"{CHART_TITLES['timeline']} — {code}")
        if opt.get("extra_charts"):
            for k in ("throughput", "vus", "errors"):
                if k == "errors" and not s.get("errors"):
                    continue
                rb.figure(ch.get(k), f"{CHART_TITLES[k]} — {code}")
    if opt.get("extra_charts") and len(labels) > 1:
        rb.figure(ch.get("api"), f"{CHART_TITLES['api']} — {code}")
    rb.labeled("Nhận định: ", r["verdict"].replace("Nhận định: ", ""))
    if opt.get("insights"):
        for line in r.get("insights", []):
            rb.bullet(line)
    _resource_block(rb, r, ch, opt)


def _mon_slice(r: dict, opt: dict):
    """Số liệu tài nguyên máy chủ trong khung giờ của lượt chạy (None nếu không có)."""
    mon = opt.get("_mon")
    if mon is None or mon.empty:
        return None
    from ..monitor.store import run_slice
    sl = run_slice(mon, r.get("started_at", ""), r.get("finished_at", ""))
    return None if sl.empty else sl


def _resource_recs(rows: list[dict], opt: dict) -> list[str]:
    """Kiến nghị khi đã có số liệu tài nguyên máy chủ: bỏ câu "cần thu thập thêm", thêm kiến nghị theo mức sử dụng."""
    slices = [s for s in (_mon_slice(r, opt) for r in rows) if s is not None]
    if not slices:
        return []
    import pandas as pd
    from ..monitor import METRICS
    from ..monitor.store import summarize
    recs = [x for x in recommendations(rows) if not x.startswith("Thu thập thêm số liệu tài nguyên máy chủ")]
    peak: dict[tuple[str, str], float] = {}
    for x in summarize(pd.concat(slices)):
        if x["metric"] in ("cpu", "ram", "disk_busy"):
            peak[(x["server"], x["metric"])] = x["max"]
    hot = [f"{METRICS[m][0]} máy chủ {sv} ({v:.0f}%)" for (sv, m), v in peak.items() if v >= 90]
    if hot:
        recs.append(f"Tài nguyên chạm mức gần cạn khi kiểm thử: {', '.join(hot)} – cân nhắc tối ưu ứng dụng/truy vấn "
                    "hoặc nâng cấp phần cứng trước khi tăng số người dùng.")
    elif peak:
        top = max(peak.items(), key=lambda kv: kv[1])
        recs.append(f"Tài nguyên máy chủ chưa chạm giới hạn (cao nhất {METRICS[top[0][1]][0]} {top[0][0]} {top[1]:.0f}%); "
                    "có thể tăng mức tải (Stress Test) để tìm điểm nghẽn thực sự.")
    return recs


def _monitor_text(project: Project, opt: dict) -> str:
    from ..models import MONITOR_TOOLS
    m = project.monitor
    if not m.enabled or opt.get("_mon") is None or opt["_mon"].empty:
        return "không thu thập"
    parts = [MONITOR_TOOLS.get(t, t) for t in m.tools]
    if m.db.enabled:
        from ..models import DB_TYPES
        parts.append(f"truy vấn số kết nối {DB_TYPES.get(m.db.db_type, m.db.db_type)}")
    return f"{', '.join(parts)} – {len(m.servers)} máy chủ, lấy mẫu mỗi {m.interval_s} giây"


def _resource_block(rb: ReportBuilder, r: dict, ch: dict[str, str], opt: dict) -> None:
    sl = _mon_slice(r, opt)
    if sl is None:
        return
    from ..monitor.store import fmt_val, insights as mon_insights, summarize
    summ = summarize(sl)
    rb.para("Tài nguyên máy chủ trong thời gian kiểm thử:", bold=True, after=60)
    rb.table(["STT", "Máy chủ", "Chỉ số", "Trung bình", "Cao nhất", "Số mẫu"],
             [[str(i), x["server"], f"{x['label']} ({x['unit']})", fmt_val(x["avg"], x["unit"]),
               fmt_val(x["max"], x["unit"]), str(x["n"])] for i, x in enumerate(summ, 1)],
             widths=[567, 2300, 2300, 1400, 1400, 1100], center_cols=(0, 3, 4, 5))
    rb.figure(ch.get("resources"), f"{CHART_TITLES['resources']} — {r['uc_code']}")
    for line in mon_insights(summ):
        rb.bullet(line)


def _rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT_DIR)).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return path


def export_excel(rows: list[dict], out_file: Path, mon=None) -> Path:
    """mon: DataFrame số liệu tài nguyên máy chủ (cấu hình ở bước 6) – có thì thêm sheet "Tai nguyen may chu"."""
    import pandas as pd
    summ = [{"Mã UC": r["uc_code"], "Tên UC": r["uc_name"], "Phân hệ": r.get("module", ""), "Công cụ": r["tool"],
             "Kịch bản": scenario_name(r["scenario_type"]), "VUs": r["config"].get("vus"), "Số request": r["summary"].get("samples"),
             "Thông lượng (req/s)": round(r["summary"].get("throughput") or 0, 2),
             "TB (ms)": r["summary"].get("avg"), "Min (ms)": r["summary"].get("min"), "p50": r["summary"].get("p50"),
             "p90": r["summary"].get("p90"), "p95": r["summary"].get("p95"), "p99": r["summary"].get("p99"),
             "Max (ms)": r["summary"].get("max"), "Tỷ lệ lỗi": r["summary"].get("error_rate"),
             "Max VUs": r["summary"].get("max_vus"), "Ngưỡng p95": r["p95_threshold"],
             "Kết luận": _verdict(r["evaluation"]["passed"]), "Lượt chạy": r["run_id"]} for r in rows]
    labels = [{**l, "run_id": r["run_id"], "tool": r["tool"]} for r in rows for l in (r.get("labels") or [])]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["scenario_type"], r["tool"]), []).append(r)
    overview = [{"Kịch bản": scenario_name(t), "Công cụ": tool, "Số UC": len(g),
                 "VUs tối đa": max(r["summary"].get("max_vus") or 0 for r in g),
                 "Tổng request": sum(r["summary"].get("samples", 0) for r in g),
                 "Thông lượng TB (req/s)": round(sum(r["summary"].get("throughput") or 0 for r in g) / len(g), 2),
                 "p95 nhỏ nhất (ms)": round(min(r["summary"].get("p95") or 0 for r in g)),
                 "p95 lớn nhất (ms)": round(max(r["summary"].get("p95") or 0 for r in g)),
                 "Tỷ lệ lỗi": (sum(r["summary"].get("errors", 0) for r in g)
                               / max(sum(r["summary"].get("samples", 0) for r in g), 1)),
                 "Đạt": sum(1 for r in g if r["evaluation"]["passed"])} for (t, tool), g in groups.items()]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_file, engine="openpyxl") as xw:
        pd.DataFrame(overview).to_excel(xw, sheet_name="Tong quan", index=False)
        pd.DataFrame([{"Phân hệ": m["module"], "Số UC": len(m["ucs"]), "UC": ", ".join(m["ucs"]),
                       "Số kịch bản": m["runs"], "Số kịch bản đạt": m["passed"], "p95 cao nhất (ms)": round(m["p95_max"]),
                       "UC có p95 cao nhất": m["p95_worst_uc"], "Tỷ lệ lỗi": m["error_rate"],
                       "Thông lượng TB (req/s)": round(m["throughput"], 2), "Kết luận": m["verdict"],
                       "Nhận định": m["text"]} for m in module_summaries(rows)]).to_excel(
            xw, sheet_name="Theo phan he", index=False)
        pd.DataFrame(summ).to_excel(xw, sheet_name="Tong hop", index=False)
        pd.DataFrame(labels).to_excel(xw, sheet_name="Theo request", index=False)
        if mon is not None and not mon.empty:
            from ..monitor.store import run_slice, summarize
            res = [{"Mã UC": r["uc_code"], "Công cụ": r["tool"], "Kịch bản": scenario_name(r["scenario_type"]),
                    "Máy chủ": x["server"], "Chỉ số": x["label"], "Đơn vị": x["unit"], "Trung bình": round(x["avg"], 2),
                    "Cao nhất": round(x["max"], 2), "Số mẫu": x["n"], "Lượt chạy": r["run_id"]}
                   for r in rows for x in summarize(run_slice(mon, r.get("started_at", ""), r.get("finished_at", "")))]
            if res:
                pd.DataFrame(res).to_excel(xw, sheet_name="Tai nguyen may chu", index=False)
    return out_file


def try_export_pdf(docx_file: Path) -> Optional[Path]:
    """Chuyển sang PDF nếu máy có Microsoft Word (qua COM). Trả về None nếu không khả dụng."""
    try:
        import win32com.client  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    pdf = docx_file.with_suffix(".pdf")
    word = win32com.client.Dispatch("Word.Application")
    try:
        d = word.Documents.Open(str(docx_file.resolve()))
        d.SaveAs(str(pdf.resolve()), FileFormat=17)
        d.Close()
    finally:
        word.Quit()
    return pdf

