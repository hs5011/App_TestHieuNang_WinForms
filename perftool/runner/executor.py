"""Chuẩn bị và thực thi các lượt chạy k6 / JMeter."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .. import config
from ..accounts import write_secret_files
from ..models import Project, RunInfo, TestScenario
from ..scriptgen.generator import generate_jmeter, generate_k6
from ..storage import sub_dir

Log = Callable[[str], None]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def plan_runs(project: Project) -> list[RunInfo]:
    """Sinh script và tạo danh sách lượt chạy theo cấu hình (tuần tự từng UC hoặc gộp)."""
    cfg = project.test_config
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    groups: list[tuple[str, list[TestScenario]]]
    if cfg.run_mode == "combined":
        groups = [("ALL", project.scenarios)]
    else:
        groups = [(sc.uc_code, [sc]) for sc in project.scenarios]
    runs: list[RunInfo] = []
    for tool in cfg.tools:
        used: set[str] = set()
        for code, scs in groups:
            if not any(s.enabled for sc in scs for s in sc.steps):
                continue
            safe = _dir_safe(code)
            base, k = safe, 2
            while safe.lower() in used:          # "UC:1" và "UC*1" cùng thành "UC_1" -> thêm hậu tố
                safe, k = f"{base}_{k}", k + 1
            used.add(safe.lower())
            run_id = f"{stamp}_{tool}_{cfg.scenario_type}_{safe}"
            d = sub_dir(project.id, f"runs/{run_id}")
            if tool == "k6":
                script = generate_k6(project, scs, d / "script.js")
                raw, summary = d / "raw.csv", d / "summary.json"
            else:
                script = generate_jmeter(project, scs, d / "plan.jmx")
                raw, summary = d / "results.jtl", d / "html-report"
            runs.append(RunInfo(run_id=run_id, tool=tool, uc_code=code, scenario_type=cfg.scenario_type,
                                script_file=str(script), raw_file=str(raw), summary_file=str(summary),
                                log_file=str(d / "tool.log"), config=cfg.model_dump()))
    return runs


_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _dir_safe(code: str) -> str:
    """Mã UC -> tên thư mục hợp lệ trên Windows (bỏ < > : " / \\ | ? *, ký tự điều khiển, dấu chấm/cách ở cuối).
    Mã UC gốc vẫn giữ nguyên trong run.json (uc_code)."""
    s = _WIN_BAD.sub("_", code or "").strip(" .")
    return s[:60].rstrip(" .") or "UC"


def build_command(run: RunInfo, secrets_file: Optional[Path]) -> list[str]:
    if run.tool == "k6":
        exe = config.k6_executable()
        if not exe:
            raise FileNotFoundError("Không tìm thấy k6. Cài đặt: winget install k6 --source winget")
        return [exe, "run", "--out", f"csv={run.raw_file}", f"--summary-export={run.summary_file}",
                "--no-color", run.script_file]
    exe = config.jmeter_executable()
    if not exe:
        raise FileNotFoundError("Không tìm thấy JMeter. Khai báo tools.jmeter_path trong config/settings.yaml")
    cmd = [exe, "-n", "-t", run.script_file, "-l", run.raw_file, "-j", str(Path(run.log_file).with_name("jmeter.log")),
           "-Jjmeter.save.saveservice.output_format=csv", "-Jjmeter.save.saveservice.thread_counts=true",
           "-Jjmeter.save.saveservice.latency=true", "-Jjmeter.save.saveservice.url=true",
           # không ghi các bước chuyển hướng con ("Mở màn hình-0", "-1"): thời gian của chúng đã nằm trong mẫu chính,
           # ghi thêm sẽ bị đếm trùng vào số request / thông lượng / p95
           "-Jjmeter.save.saveservice.subresults=false",
           "-Jsummariser.interval=10", "-e", "-o", run.summary_file]
    if secrets_file:
        cmd += ["-q", str(secrets_file)]
    return cmd


def clear_previous_outputs(run: RunInfo) -> list[Path]:
    """Xoá kết quả cũ của lượt chạy (k6: raw.csv, summary.json; JMeter: results.jtl, html-report/).

    Khi chạy lại một lượt đã có kết quả, JMeter từ chối ghi vào html-report không rỗng và thoát lỗi,
    còn file raw cũ vẫn nằm đó -> bị hiểu nhầm là kết quả mới. Chỉ xoá đường dẫn nằm ngay trong
    thư mục của lượt chạy này. Trả về danh sách đường dẫn đã xoá.
    """
    run_dir = Path(run.script_file).parent.resolve()
    removed: list[Path] = []
    for f in (run.raw_file, run.summary_file):
        if not f:
            continue
        p = Path(f)
        if p.resolve().parent != run_dir:  # an toàn: không đụng tới file ngoài thư mục lượt chạy
            continue
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            continue
        removed.append(p)
    return removed


def has_fresh_data(run: RunInfo) -> bool:
    """File dữ liệu thô tồn tại, có ít nhất 1 dòng dữ liệu ngoài dòng tiêu đề và được ghi sau thời điểm bắt đầu lượt."""
    p = Path(run.raw_file)
    if not p.exists() or p.stat().st_size == 0 or not _has_data_row(p):
        return False
    if not run.started_at:
        return True
    try:
        started = datetime.strptime(run.started_at, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return True
    # started_at chỉ chính xác tới giây -> so với mốc đầu giây đó
    return p.stat().st_mtime >= started


def _has_data_row(p: Path) -> bool:
    """Công cụ crash ngay sau khi mở file chỉ kịp ghi dòng tiêu đề CSV -> không tính là có dữ liệu."""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            fh.readline()
            return any(line.strip() for line in fh)
    except OSError:
        return False


def execute(run: RunInfo, accounts: list[tuple[str, str]], log: Log,
            on_start: Callable[[int], None] = lambda p: None) -> int:
    """Chạy 1 lượt test. accounts: danh sách (tài khoản, mật khẩu); phần tử đầu là tài khoản chính/dự phòng."""
    run_dir = Path(run.script_file).parent
    for p in clear_previous_outputs(run):
        log(f"Đã xoá kết quả cũ: {p.name}")
    username, password = accounts[0] if accounts else ("", "")
    # không chuyển mật khẩu của cả nhóm tài khoản / máy chủ (PERFTOOL_ACCOUNTS, PERFTOOL_MONITOR_SECRETS…) sang k6/JMeter
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PERFTOOL_")}
    env["PERF_USERNAME"] = username
    env["PERF_PASSWORD"] = password
    secrets: Optional[Path] = None
    mode_multi = (run.config or {}).get("account_mode", "multi") == "multi"
    pool = accounts if mode_multi else accounts[:1]
    # k6 chỉ cần file khi có >1 tài khoản (không có file -> dùng biến môi trường);
    # JMeter: kế hoạch đọc accounts.csv (JSR223 chọn tài khoản cho từng VU) khi ở chế độ nhiều tài khoản -> luôn cần file.
    temp_files = write_secret_files(run_dir, run.tool, pool if (run.tool == "jmeter" or len(pool) > 1) else [])
    if len(pool) > 1:
        log(f"Dùng {len(pool)} tài khoản kiểm thử, chia đều cho các người dùng ảo")
    if run.tool == "jmeter":
        secrets = run_dir / "secrets.properties"
        secrets.write_text(f"PERF_USERNAME={_prop(username)}\nPERF_PASSWORD={_prop(password)}\n", encoding="utf-8")
        java_home = env.get("JAVA_HOME")
        if not java_home:
            env.setdefault("JVM_ARGS", "-Xms512m -Xmx2g")
    try:
        cmd = build_command(run, secrets)
        log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with open(run.log_file, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(cmd, cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", creationflags=flags)
            on_start(proc.pid)
            for line in proc.stdout:  # type: ignore[union-attr]
                lf.write(line)
                lf.flush()
                s = line.rstrip()
                if s:
                    log(f"[{run.tool}] {s}")
            rc = proc.wait()
        return rc
    finally:
        for f in [secrets, *temp_files]:
            if f and f.exists():
                f.unlink()


def _prop(v: str) -> str:
    """Escape giá trị cho file .properties của Java.

    JMeter nạp file -q bằng Properties.load(InputStream): đọc theo ISO-8859-1 và bỏ khoảng trắng đầu giá trị
    -> ký tự ngoài ASCII ghi dạng \\uXXXX, khoảng trắng đầu ghi "\\ " (vd mật khẩu "Mật khẩu@2026").
    """
    special = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "=": "\\=", ":": "\\:", "#": "\\#", "!": "\\!"}
    out = []
    for i, ch in enumerate(v):
        if ch in special:
            out.append(special[ch])
        elif ch == " " and i == 0:
            out.append("\\ ")
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            b = ch.encode("utf-16-be")          # ký tự ngoài BMP (emoji) -> cặp surrogate
            out.extend(f"\\u{int.from_bytes(b[j:j + 2], 'big'):04x}" for j in range(0, len(b), 2))
    return "".join(out)


def save_run_file(run: RunInfo) -> None:
    Path(run.script_file).parent.joinpath("run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")


def load_run_file(run_dir: Path) -> Optional[RunInfo]:
    f = run_dir / "run.json"
    if not f.exists():
        return None
    try:
        return RunInfo.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def python_exe() -> str:
    return sys.executable
