"""Tiện ích chung cho sinh script: phân tích thời lượng, hồ sơ tải theo loại kịch bản."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..models import TestConfig

# Tên hiển thị thống nhất cho mọi màn hình / biểu đồ / báo cáo (mã nội bộ smoke, load… giữ nguyên trong dữ liệu)
SCENARIO_NAMES = {
    "smoke": "Smoke Test",
    "load": "Load Test",
    "stress": "Stress Test",
    "spike": "Spike Test",
    "soak": "Soak Test",
}
SCENARIO_ORDER = list(SCENARIO_NAMES)
SCENARIO_TYPES = {
    "smoke": "Smoke Test – chạy ít tải, kiểm tra hệ thống & kịch bản hoạt động ổn trước khi test lớn",
    "load": "Load Test – chạy đúng tải dự kiến (ramp-up → giữ tải → ramp-down)",
    "stress": "Stress Test – chạy vượt tải, tăng dần theo bậc tới 150% tải mục tiêu để tìm giới hạn",
    "spike": "Spike Test – tải tăng đột ngột lên mức mục tiêu rồi giảm",
    "soak": "Soak Test – chạy tải liên tục trong thời gian dài",
}


def scenario_name(key: str) -> str:
    """'smoke' -> 'Smoke Test' (giữ nguyên nếu không nhận ra)."""
    return SCENARIO_NAMES.get(key, key)


def scenario_rank(key: str) -> int:
    return SCENARIO_ORDER.index(key) if key in SCENARIO_ORDER else 9


_DUR_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)")
_DUR_FULL = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:ms|h|m|s)\s*)+")


def parse_duration(s: str | int | float) -> int:
    """'1h30m', '5m', '45s', '90' -> số giây (làm tròn lên: '500ms' -> 1). Chuỗi sai định dạng -> 0
    (xem valid_duration để kiểm tra đầu vào của người dùng)."""
    if isinstance(s, (int, float)):
        return max(int(s), 0)
    s = (s or "").strip().lower()
    if s.isdigit():
        return int(s)
    if not _DUR_FULL.fullmatch(s):
        return 0
    total = sum(float(n) * {"ms": 0.001, "h": 3600, "m": 60, "s": 1}[u] for n, u in _DUR_PART.findall(s))
    return int(math.ceil(total - 1e-9))


def valid_duration(s: str, allow_zero: bool = False) -> bool:
    """Đầu vào thời lượng hợp lệ: chỉ gồm số + đơn vị (1h30m, 5m, 45s, 500ms) hoặc số giây; không âm, không phần
    thừa ('1m30' thiếu đơn vị, '-5m', chuỗi có xuống dòng + mã lạ đều bị từ chối)."""
    v = (s or "").strip().lower()
    if not (v.isdigit() or _DUR_FULL.fullmatch(v)):
        return False
    return allow_zero or parse_duration(v) > 0


def fmt_duration(sec: int) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return "".join(f"{v}{u}" for v, u in ((h, "h"), (m, "m"), (s, "s")) if v) or "0s"


@dataclass
class Stage:
    duration_s: int
    target: int


def stages_for(cfg: TestConfig, vus: int | None = None) -> list[Stage]:
    vus = max(1, int(vus or cfg.vus))
    up, hold, down = parse_duration(cfg.ramp_up), parse_duration(cfg.duration), parse_duration(cfg.ramp_down)
    t = cfg.scenario_type
    if t == "smoke":
        # tăng nhanh lên đủ VUs rồi giữ cố định (tránh k6 ramp tuyến tính suốt cả phiên)
        return [Stage(5, vus), Stage(max(hold, 30), vus)]
    if t == "stress":
        step = max(hold // 5, 30)
        lv = [max(1, int(vus * k)) for k in (0.5, 1, 1.25, 1.5)]     # vus=1: bậc 50% vẫn có 1 VU (không chạy rỗng)
        return [Stage(up, lv[0]), Stage(step, lv[0]), Stage(up, lv[1]), Stage(step, lv[1]),
                Stage(up, lv[2]), Stage(step, lv[2]), Stage(up, lv[3]), Stage(step, lv[3]), Stage(down, 0)]
    if t == "spike":
        return [Stage(10, max(1, vus // 10)), Stage(max(up // 4, 10), vus), Stage(max(hold, 30), vus),
                Stage(10, max(1, vus // 10)), Stage(30, max(1, vus // 10)), Stage(max(down, 10), 0)]
    # load / soak
    return [Stage(up, vus), Stage(hold, vus), Stage(down, 0)]


@dataclass
class ThreadBlock:
    """1 Thread Group của JMeter: `threads` luồng, bắt đầu sau `delay_s`, tăng dần trong `ramp_s`, sống `duration_s`."""
    threads: int
    delay_s: int
    ramp_s: int
    duration_s: int


def jmeter_blocks(stages: list[Stage]) -> list[ThreadBlock]:
    """Quy hồ sơ tải bậc thang của k6 (ramping-vus) về các Thread Group chuẩn của JMeter (không cần plugin):
    mỗi lần tăng tải = 1 nhóm luồng mới có độ trễ khởi động; mỗi lần giảm tải = kết thúc các nhóm vào sau cùng.
    Giảm tải của JMeter là tức thời (ở đầu giai đoạn ramp-down), k6 giảm tuyến tính."""
    t, cur = 0, 0
    open_: list[list[int]] = []          # [threads, delay, ramp]
    out: list[ThreadBlock] = []

    def close(g: list[int], n: int, end: int) -> None:
        if end - g[1] > 0 and n > 0:
            out.append(ThreadBlock(n, g[1], min(g[2], end - g[1]), end - g[1]))

    for st in stages:
        if st.target > cur:
            open_.append([st.target - cur, t, st.duration_s])
        elif st.target < cur:
            excess = cur - st.target
            while excess > 0 and open_:
                g = open_[-1]
                if g[0] <= excess:
                    open_.pop()
                    excess -= g[0]
                    close(g, g[0], t)
                else:
                    g[0] -= excess
                    close(g, excess, t)
                    excess = 0
        cur = st.target
        t += st.duration_s
    for g in open_:
        close(g, g[0], t)
    return sorted(out, key=lambda b: (b.delay_s, -b.duration_s))


def total_seconds(cfg: TestConfig) -> int:
    return sum(s.duration_s for s in stages_for(cfg))


def split_vus(total: int, n: int) -> list[int]:
    base, rem = divmod(max(total, n), n)
    return [base + (1 if i < rem else 0) for i in range(n)]
