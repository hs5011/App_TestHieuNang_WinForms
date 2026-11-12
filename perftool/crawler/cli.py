"""CLI chạy crawler ở tiến trình nền.

  python -m perftool.crawler.cli --project <id> --mode discover
  python -m perftool.crawler.cli --project <id> --mode analyze
  python -m perftool.crawler.cli --project <id> --mode record [--module <phân hệ>] [--guard keyword|strict|off]
                                 [--start-url <url>]

Mật khẩu được truyền qua biến môi trường PERFTOOL_PASSWORD (không qua dòng lệnh).
Kết quả ghi vào workspace/projects/<id>/crawl/{discover,analysis,record}.json;
giao diện sẽ đọc và hợp nhất vào project.json.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime

from .. import config, jobs
from ..storage import SESSION_FIELDS, load_project, sub_dir
from .interactions import join_url
from .web_crawler import WebCrawler, dump


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", choices=["discover", "analyze", "record"], required=True)
    ap.add_argument("--fresh-login", action="store_true", help="Bỏ qua phiên đăng nhập đã lưu")
    ap.add_argument("--module", default="", help="(record) gán mọi màn hình ghi được vào phân hệ này")
    ap.add_argument("--guard", default="keyword", choices=["keyword", "strict", "off"], help="(record) mức chặn ghi")
    ap.add_argument("--start-url", default="", help="(record) trang mở đầu tiên")
    a = ap.parse_args()

    job = f"crawl_{a.mode}"
    project = load_project(a.project)
    work = sub_dir(a.project, "crawl")
    password = os.environ.get("PERFTOOL_PASSWORD", "")
    if a.mode == "record":
        project.login.headless = False          # người dùng phải thấy trình duyệt để thao tác
    try:
        with WebCrawler(project.login, password, work, log) as c:
            if not c.login(reuse_state=not a.fresh_login):
                jobs.write_status(a.project, job, status="failed", finished=_now(), message="Đăng nhập thất bại")
                return 2
            if a.mode != "record":
                # luôn bật bộ chặn ghi khi crawler tự điều hướng (kể cả khi chỉ mở rộng menu / tải trang):
                # trang tự gửi request ghi (vd đánh dấu đã xem) cũng bị huỷ; khi bấm thử tương tác tự chuyển mức "strict"
                # mức mặc định "strict": POST lúc tải trang mà không mang tên truy vấn (vd /api/log/visit, /api/hoso/view –
                # ghi nhận lượt xem) cũng bị huỷ; đổi bằng crawler.page_load_guard trong settings.yaml
                c.enable_write_guard(str(config.get("crawler.page_load_guard", "strict")))
            if a.mode == "discover":
                mods = c.discover_modules()
                dump(work / "discover.json", {"home": c.page.url, "modules": mods,
                                              "auth": c.auth.model_dump(exclude=SESSION_FIELDS)})
                log(f"Hoàn tất phát hiện {len(mods)} phân hệ.")
            elif a.mode == "record":
                from .manual import ManualRecorder
                rec = ManualRecorder(c, work / "record.json", work / "record.stop", a.module, a.guard)
                rec.run(a.start_url or project.login.base_url)
            else:
                c.scan_popups = project.crawl_popups
                if c.scan_popups:
                    log("Bật quét tương tác: bấm thử tab / Tìm kiếm / chuyển trang / nút Xem-Thêm-Sửa / menu ⋮ trên dòng "
                        "đầu bảng; không bấm nút trong popup, không bấm mục nguy hiểm (Xoá, Duyệt, Hoàn thành…); "
                        "trong lúc bấm thử, bộ chặn ghi chỉ cho qua request truy vấn (GetAll/Search…).")
                shots = sub_dir(a.project, "crawl/screens")
                result = {"auth": c.auth.model_dump(exclude=SESSION_FIELDS), "modules": [], "partial": True}
                (work / "analysis.json").unlink(missing_ok=True)   # lần quét này bị dừng sớm -> không gộp lại kết quả cũ
                enabled = [m for m in project.modules if m.enabled]
                # Thêm các URL gợi ý từ file UC (nếu có)
                hints = [{"href": join_url(project.login.base_url, u.url_hint), "text": u.name}
                         for u in project.use_cases if u.url_hint]
                links_file = work / "discover.json"
                disc = {}
                if links_file.exists():
                    import json
                    disc = {m["name"]: m["links"] for m in json.loads(links_file.read_text("utf-8"))["modules"]}
                total_steps = len(enabled) + (1 if hints else 0)
                for i, m in enumerate(enabled, 1):
                    jobs.write_status(a.project, job, progress=f"{i}/{total_steps} phân hệ")
                    log(f"Phân hệ {i}/{len(enabled)}: {m.name}")
                    links = disc.get(m.name) or [{"href": m.url, "text": m.name}]
                    if m.url and all(ln["href"] != m.url for ln in links):
                        links.insert(0, {"href": m.url, "text": m.name})
                    pages = c.analyze_module(m.name, links, shots)
                    result["modules"].append({"name": m.name, "url": m.url, "pages": [p.model_dump() for p in pages]})
                    result["auth"] = c.auth.model_dump(exclude=SESSION_FIELDS)
                    dump(work / "analysis.json", result)       # lưu dần: bấm Dừng vẫn giữ các phân hệ đã quét xong
                if hints:
                    jobs.write_status(a.project, job, progress=f"{total_steps}/{total_steps} · URL gợi ý")
                    log(f"Quét {len(hints)} URL gợi ý từ danh sách UC")
                    # không áp giới hạn số trang/phân hệ cho URL gợi ý: mỗi URL do người dùng khai báo đều được quét
                    pages = c.analyze_module("URL gợi ý từ UC", hints, shots, max_pages=len(hints))
                    result["modules"].append({"name": "URL gợi ý từ UC", "url": hints[0]["href"],
                                              "pages": [p.model_dump() for p in pages]})
                result["auth"] = c.auth.model_dump(exclude=SESSION_FIELDS)
                result["partial"] = False
                dump(work / "analysis.json", result)
                total = sum(len(m["pages"]) for m in result["modules"])
                log(f"Hoàn tất phân tích {len(result['modules'])} phân hệ, {total} màn hình."
                    + (f" Bộ chặn ghi đã huỷ {c.guard.count} request ghi dữ liệu." if c.guard and c.guard.count else ""))
            c.save_session()        # Authorization bắt được trong lúc quét -> crawl/auth.json (không vào file kết quả)
        jobs.write_status(a.project, job, status="done", finished=_now(), message="Hoàn tất")
        return 0
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        jobs.write_status(a.project, job, status="failed", finished=_now(), message=str(e)[:300])
        return 1


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
