"""Windows Performance Monitor: danh sách bộ đếm và bộ đọc định dạng PDH-CSV (typeperf / relog / Save Data As)."""
from __future__ import annotations

import csv
import io
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

COUNTERS = [
    r"\Processor(_Total)\% Processor Time",
    r"\Memory\Available MBytes",
    r"\Memory\% Committed Bytes In Use",
    r"\PhysicalDisk(_Total)\% Idle Time",
    r"\PhysicalDisk(_Total)\Disk Read Bytes/sec",
    r"\PhysicalDisk(_Total)\Disk Write Bytes/sec",
    r"\Network Interface(*)\Bytes Total/sec",
]

# lệnh cho quản trị viên tự ghi log trên máy chủ khi không có SSH (xuất thẳng CSV, mỗi 5 giây)
LOGMAN_CREATE = ('logman create counter PerfTool -f csv -si 5 -o "C:\\PerfLogs\\PerfTool" -c '
                 + " ".join(f'"{c}"' for c in COUNTERS))
LOGMAN_START, LOGMAN_STOP = "logman start PerfTool", "logman stop PerfTool"

TOTAL_RAM_CMD = 'powershell -NoProfile -Command "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"'


def typeperf_cmd(interval_s: int, samples: Optional[int] = None) -> str:
    cmd = "typeperf " + " ".join(f'"{c}"' for c in COUNTERS) + f" -si {int(interval_s)}"
    return cmd + (f" -sc {int(samples)}" if samples else "")


def _kind(col: str) -> Optional[str]:
    c = col.lower()
    if "% processor time" in c:
        return "cpu"
    if "available mbytes" in c:
        return "avail_mb"
    if "% committed bytes in use" in c:
        return "committed"
    if "physicaldisk" in c and "% idle time" in c:
        return "disk_idle"
    if "disk read bytes/sec" in c:
        return "disk_read"
    if "disk write bytes/sec" in c:
        return "disk_write"
    if "network interface" in c and "bytes total/sec" in c:
        return "net"
    return None


def host_of(col: str) -> str:
    r"""'\\SERVER01\Processor(_Total)\...' -> 'SERVER01'."""
    return col[2:].split("\\", 1)[0] if col.startswith("\\\\") else ""


def _num(v: str) -> Optional[float]:
    try:
        return float(v.strip())
    except (ValueError, AttributeError):
        return None


def _ts(v: str) -> Optional[datetime]:
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    return None


class PdhParser:
    """Đọc từng dòng PDH-CSV, trả về các bản ghi (ts, metric, value) đã quy đổi đơn vị.

    ram_total_mb: nếu biết tổng RAM thì % RAM = 100 - Available/Tổng, ngược lại dùng % Committed Bytes In Use.
    """

    def __init__(self, ram_total_mb: Optional[float] = None):
        self.cols: list[Optional[str]] = []
        self.hosts: set[str] = set()
        self.ram_total_mb = ram_total_mb

    def feed(self, line: str, ts_override: Optional[datetime] = None) -> list[tuple[datetime, str, float]]:
        line = line.strip()
        if not line.startswith('"'):
            return []               # dòng thông báo của typeperf ("Exiting, please wait...")
        vals = next(csv.reader([line]))
        if vals and vals[0].startswith("(PDH-CSV"):
            self.cols = [_kind(c) for c in vals]
            self.hosts = {h for h in (host_of(c) for c in vals[1:]) if h}
            return []
        if not self.cols or len(vals) != len(self.cols):
            return []
        ts = ts_override or _ts(vals[0])
        if ts is None:
            return []
        got: dict[str, float] = {}
        for kind, v in zip(self.cols[1:], vals[1:]):
            x = _num(v)
            if kind is None or x is None:
                continue
            got[kind] = got.get(kind, 0.0) + x if kind == "net" else x   # nhiều card mạng -> cộng dồn
        out: list[tuple[datetime, str, float]] = []
        if "cpu" in got:
            out.append((ts, "cpu", got["cpu"]))
        if "avail_mb" in got and self.ram_total_mb:
            out.append((ts, "ram", max(0.0, 100 - got["avail_mb"] / self.ram_total_mb * 100)))
        elif "committed" in got:
            out.append((ts, "ram", got["committed"]))
        if "disk_idle" in got:
            out.append((ts, "disk_busy", max(0.0, 100 - got["disk_idle"])))
        for k in ("disk_read", "disk_write", "net"):
            if k in got:
                out.append((ts, k, got[k] / 1024 / 1024))
        return out


def parse_file(path: Path, ram_total_mb: Optional[float] = None) -> tuple[list[tuple[datetime, str, float]], set[str]]:
    """Đọc file .csv (PDH-CSV) hoặc .blg (chuyển sang CSV bằng relog có sẵn trên Windows)."""
    if path.suffix.lower() == ".blg":
        csv_path = path.with_suffix(".relog.csv")
        r = subprocess.run(["relog", str(path), "-f", "csv", "-o", str(csv_path), "-y"], capture_output=True,
                           text=True, timeout=300)
        if not csv_path.exists():
            raise RuntimeError(f"Không chuyển được file .blg bằng relog: {(r.stdout or r.stderr).strip()[:300]}")
        path = csv_path
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="replace")
    p = PdhParser(ram_total_mb)
    rows: list[tuple[datetime, str, float]] = []
    for line in io.StringIO(text):
        rows.extend(p.feed(line))
    if not p.cols:
        raise ValueError("File không đúng định dạng Performance Monitor (thiếu dòng tiêu đề PDH-CSV).")
    return rows, p.hosts
