"""Bước 7 - Cấu hình tải, sinh script và chạy test."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ... import config, jobs, storage
from ...models import Project, RunInfo
from ...runner.executor import load_run_file, plan_runs, save_run_file
from ...scriptgen.profile import (SCENARIO_TYPES, fmt_duration, parse_duration, scenario_name, stages_for,
                                  total_seconds, valid_duration)
from .. import state
from ..components import job_panel, section_titles

JOB = "run_tests"
AUTH_MODES = {"login_per_vu": "Mỗi VU đăng nhập 1 lần (khuyến nghị)",
              "static_headers": "Dùng cookie/token phiên đã ghi nhận",
              "none": "Không xác thực"}


def sync_runs(p: Project) -> None:
    """Cập nhật trạng thái lượt chạy từ file run.json do tiến trình nền ghi."""
    changed = False
    job_alive = jobs.is_running(p.id, JOB)
    for i, r in enumerate(p.runs):
        f = load_run_file(Path(r.script_file).parent)
        if f and f.status == "running" and not job_alive:
            # tiến trình nền đã bị dừng/kết thúc giữa chừng -> lượt chưa chạy hết, KHÔNG coi là "done"
            # (bước 8–10 chỉ phân tích lượt done); giờ kết thúc = lần cuối file kết quả được ghi
            raw = Path(f.raw_file)
            f.status = "stopped"
            f.finished_at = (datetime.fromtimestamp(raw.stat().st_mtime) if raw.exists() and raw.stat().st_size > 0
                             else datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
            save_run_file(f)
        if f and (f.status != r.status or f.finished_at != r.finished_at):
            p.runs[i] = f
            changed = True
    if changed:
        state.save(p)


STATUS_VI = {"pending": "🕓 Chờ chạy", "running": "⏳ Đang chạy", "done": "✅ Đã chạy xong", "failed": "❌ Lỗi",
             "stopped": "⏹ Đã dừng"}
_K6_PCT = re.compile(r"\[\s*(\d+)%\s*\]")


def _progress(r: RunInfo) -> float:
    """Tiến độ 0..1 của một lượt chạy (k6: đọc % từ log; JMeter: thời gian đã chạy / thời lượng dự kiến)."""
    if r.status == "done":
        return 1.0
    if r.status != "running":
        return 0.0
    log = Path(r.log_file)
    if r.tool == "k6" and log.exists():
        try:
            with open(log, "rb") as fh:
                fh.seek(max(0, log.stat().st_size - 4000))
                m = _K6_PCT.findall(fh.read().decode("utf-8", "replace"))
            if m:
                return min(int(m[-1]) / 100, 1.0)
        except OSError:
            pass
    try:
        started = datetime.strptime(r.started_at, "%Y-%m-%d %H:%M:%S")
        cfg = r.config or {}
        planned = parse_duration(cfg.get("ramp_up", "0")) + max(parse_duration(cfg.get("duration", "0")), 30)
        return min((datetime.now() - started).total_seconds() / max(planned, 1), 0.99)
    except (ValueError, TypeError):
        return 0.0


def run_history(p: Project) -> None:
    """Bảng lịch sử lượt chạy – tự làm mới mỗi 3 giây khi tiến trình nền đang chạy."""
    running = jobs.is_running(p.id, JOB)

    @st.fragment(run_every=3 if running else None)
    def _table():
        sync_runs(p)
        alive = jobs.is_running(p.id, JOB)
        done = sum(1 for r in p.runs if r.status == "done")
        active = [r for r in p.runs if r.status in ("pending", "running")]
        if alive and active:
            cur = next((r for r in p.runs if r.status == "running"), None)
            args = (jobs.read_status(p.id, JOB) or {}).get("args", [])
            ids = set(args[args.index("--runs") + 1].split(",")) if "--runs" in args else set()
            batch = [r for r in p.runs if r.run_id in ids] or active
            finished = sum(1 for r in batch if r.status in ("done", "failed", "stopped"))
            st.progress(min((finished + (_progress(cur) if cur else 0)) / max(len(batch), 1), 1.0),
                        text=f"Đang chạy {cur.tool} · {cur.uc_code} ({_progress(cur):.0%})" if cur else "Đang chuẩn bị...")
        shown = list(reversed(p.runs))
        ev = st.dataframe(pd.DataFrame([{
            "Trạng thái": STATUS_VI.get(r.status, r.status), "Tiến độ": _progress(r), "Công cụ": r.tool,
            "UC": r.uc_code, "Kịch bản": scenario_name(r.scenario_type), "VUs": r.config.get("vus"), "Bắt đầu": r.started_at,
            "Kết thúc": r.finished_at or "", "Mã thoát": "" if r.returncode is None else str(r.returncode),
            "Lượt chạy": r.run_id} for r in shown]),
            width="stretch", hide_index=True, on_select="rerun", selection_mode="multi-row", key=f"hist_{p.id}",
            column_config={"Tiến độ": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})
        st.caption(f"{done}/{len(p.runs)} lượt đã chạy xong" + (" · tự cập nhật mỗi 3 giây" if alive else "")
                   + " · tick ô đầu dòng để chọn lượt cần xoá")
        picked = [shown[i] for i in ev.selection.rows if i < len(shown)]
        if picked:
            locked = [r for r in picked if alive and r.status in ("pending", "running")]
            with st.container(border=True):
                st.markdown(f"**Đã chọn {len(picked)} lượt:** " + ", ".join(
                    f"{r.tool} · {r.uc_code} · {scenario_name(r.scenario_type)} ({r.started_at or 'chưa chạy'})"
                    for r in picked))
                if locked:
                    st.warning("Có lượt đang chạy / chờ chạy trong tiến trình hiện tại – dừng tiến trình rồi mới xoá được.")
                c1, c2 = st.columns([3, 1], vertical_alignment="center")
                del_files = c1.checkbox("Xoá luôn thư mục kết quả trên đĩa (không khôi phục được)",
                                        key=f"hist_files_{p.id}")
                if c2.button(f"🗑 Xoá {len(picked)} lượt", type="primary", disabled=bool(locked), width="stretch",
                             key=f"hist_del_{p.id}"):
                    delete_runs(p, picked, del_files)
                    st.session_state.pop(f"hist_{p.id}", None)
                    st.toast(f"Đã xoá {len(picked)} lượt chạy" + (" và thư mục kết quả" if del_files else ""))
                    st.rerun(scope="app")
        if running and not alive:
            st.rerun(scope="app")   # vừa chạy xong -> làm mới toàn trang (mở khoá nút, cập nhật bước tiếp theo)

    _table()


def clean_history(p: Project) -> None:
    """Xoá lượt chạy khỏi lịch sử: theo trạng thái hoặc tất cả; tuỳ chọn xoá luôn thư mục kết quả trên đĩa."""
    busy = jobs.is_running(p.id, JOB)
    if busy:
        st.info("Đang có tiến trình chạy test – dừng hoặc chờ chạy xong rồi mới dọn lịch sử.")
        return
    counts: dict[str, int] = {}
    for r in p.runs:
        counts[r.status] = counts.get(r.status, 0) + 1
    order = [k for k in STATUS_VI if k in counts] + [k for k in counts if k not in STATUS_VI]
    sel = st.multiselect("Xoá theo trạng thái", order, default=[k for k in ("failed", "stopped") if k in counts],
                         format_func=lambda k: f"{STATUS_VI.get(k, k)} ({counts[k]})", key=f"clean_status_{p.id}")
    del_files = st.checkbox("Xoá luôn thư mục kết quả trên đĩa (script, log, dữ liệu thô)", key=f"clean_files_{p.id}",
                            help="Không tick: chỉ ẩn khỏi danh sách, thư mục workspace/projects/<dự án>/runs/<lượt chạy> vẫn còn. "
                                 "Tick: xoá hẳn thư mục – không khôi phục được, bước 8–10 sẽ không còn số liệu của các lượt này.")
    c1, c2 = st.columns(2)
    n_sel = sum(counts[k] for k in sel)
    do_sel = c1.button(f"Xoá {n_sel} lượt theo trạng thái đã chọn", disabled=not n_sel, width="stretch",
                       key=f"clean_sel_{p.id}")
    confirm = c2.checkbox(f"Xác nhận xoá tất cả {len(p.runs)} lượt", key=f"clean_all_ok_{p.id}")
    do_all = c2.button("🗑 Xoá tất cả", disabled=not confirm, type="primary", width="stretch", key=f"clean_all_{p.id}")
    if do_sel or do_all:
        removed = list(p.runs) if do_all else [r for r in p.runs if r.status in sel]
        delete_runs(p, removed, del_files)
        st.toast(f"Đã xoá {len(removed)} lượt chạy" + (" và thư mục kết quả" if del_files else ""))
        st.rerun()


def delete_runs(p: Project, removed: list[RunInfo], del_files: bool) -> None:
    """Gỡ các lượt chạy khỏi lịch sử dự án; tuỳ chọn xoá luôn thư mục runs/<run_id> trên đĩa."""
    if del_files:
        import shutil
        root = storage.sub_dir(p.id, "runs").resolve()
        for r in removed:
            d = Path(r.script_file).parent.resolve()
            if d.parent == root:            # chỉ xoá thư mục của đúng lượt chạy trong dự án
                shutil.rmtree(d, ignore_errors=True)
    ids = {r.run_id for r in removed}
    p.runs = [r for r in p.runs if r.run_id not in ids]
    state.save(p)


def scenario_guide(cfg) -> None:
    """Hướng dẫn 5 loại kịch bản, số liệu minh hoạ lấy theo cấu hình hiện tại."""
    n = max(1, int(cfg.vus))
    up, hold, down = cfg.ramp_up, cfg.duration, cfg.ramp_down
    step = fmt_duration(max(parse_duration(hold) // 5, 30))
    spike_up = fmt_duration(max(parse_duration(up) // 4, 10))
    st.markdown("#### 5 loại kịch bản kiểm thử")
    st.dataframe(pd.DataFrame([
        {"Loại test": "Smoke Test", "Hiểu đơn giản": "Chạy ít tải",
         "Mục đích chính": "Kiểm tra hệ thống hoạt động ổn trước khi test lớn"},
        {"Loại test": "Load Test", "Hiểu đơn giản": "Chạy đúng tải dự kiến",
         "Mục đích chính": "Kiểm tra hệ thống chịu được tải thực tế không"},
        {"Loại test": "Stress Test", "Hiểu đơn giản": "Chạy vượt tải",
         "Mục đích chính": "Tìm giới hạn hệ thống"},
        {"Loại test": "Spike Test", "Hiểu đơn giản": "Tải tăng đột ngột",
         "Mục đích chính": "Kiểm tra hệ thống phản ứng khi lượng người dùng tăng bất ngờ"},
        {"Loại test": "Soak Test", "Hiểu đơn giản": "Chạy tải liên tục trong thời gian dài",
         "Mục đích chính": "Phát hiện lỗi tích tụ, memory leak, suy giảm hiệu năng"},
    ]), hide_index=True, width="stretch",
        column_config={"Loại test": st.column_config.TextColumn(width="small"),
                       "Mục đích chính": st.column_config.TextColumn(width="large")})
    st.markdown(f"""
#### Tải được tạo ra như thế nào?
Mỗi **người dùng ảo (VU)** giống một người thật: **đăng nhập 1 lần** → thực hiện các bước của UC
(danh sách request ở bước 5) → **nghỉ** (think time {cfg.think_time_s:g} giây) → lặp lại cho tới khi hết giờ.
*Tải* chính là **số VU cùng hoạt động tại mỗi thời điểm**. 5 loại kịch bản chỉ khác nhau ở cách tăng/giảm số VU
theo thời gian, tính từ các ô: VUs = **{n}**, Ramp-up = **{up}**, Giữ tải = **{hold}**, Ramp-down = **{down}**.

- **Smoke Test**: tăng lên đủ **{n} VU trong 5 giây**, rồi giữ nguyên suốt {hold} (tối thiểu 30 giây). Không dùng
  ramp-up và ramp-down. Mục đích là kiểm tra script có chạy đúng không, nên chỉ cần vài VU (3–10).
- **Load Test**: tăng đều **0 → {n}** trong thời gian **ramp-up** ({up}), giữ {n} trong **thời lượng giữ tải**
  ({hold}), giảm về 0 trong **ramp-down** ({down}). Giai đoạn giữ tải là giai đoạn dùng để đánh giá **Đạt / Không đạt**.
- **Stress Test**: đi theo **bậc thang** lên vượt mức mục tiêu: 50% ({n // 2} VU) → 100% ({n}) → 125%
  ({int(n * 1.25)}) → 150% ({int(n * 1.5)}). Mỗi lần tăng mất {up} (ramp-up), mỗi bậc giữ khoảng {step} (1/5 thời lượng
  giữ tải). Nhìn vào bậc nào thời gian phản hồi hay lỗi bắt đầu tăng vọt thì biết **giới hạn** hệ thống nằm ở đó.
- **Spike Test**: giữ nền thấp 10% ({max(1, n // 10)} VU), rồi **vọt lên {n} trong khoảng {spike_up}**, giữ {hold},
  rồi hạ đột ngột về {max(1, n // 10)} và giữ thêm 30 giây để xem hệ thống **có hồi phục** không, tức là thời gian
  phản hồi có quay về như trước không.
- **Soak Test**: giống hệt Load Test nhưng **giữ tải rất lâu**. Hãy nhập thời lượng giữ tải dài, ví dụ `2h`. Mục đích
  là phát hiện hệ thống chậm dần theo thời gian, chẳng hạn do rò rỉ bộ nhớ hoặc cạn kết nối.

**Thứ tự nên làm:** Smoke Test → Load Test → Stress Test / Spike Test → Soak Test.
""")
    t_all = pd.DataFrame()
    # Soak có cùng hình dạng với Load (chỉ khác thời lượng giữ tải) -> không vẽ để khỏi che đường Load
    for key in [k for k in SCENARIO_TYPES if k != "soak"]:
        c = cfg.model_copy(update={"scenario_type": key})
        t, pts = 0, [(0, 0)]
        for s in stages_for(c):
            t += s.duration_s
            pts.append((t, s.target))
        t_all = pd.concat([t_all, pd.DataFrame(pts, columns=["Giây", "VUs"]).assign(**{"Kịch bản": scenario_name(key)})])
    if parse_duration(hold) <= 1800:   # thời lượng rất dài sẽ làm dẹt các đường
        st.caption("Số VU theo thời gian với cấu hình hiện tại:")
        st.line_chart(t_all, x="Giây", y="VUs", color="Kịch bản", height=220)
        st.caption("Soak Test không vẽ riêng vì có cùng hình dạng với Load Test – chỉ khác là thời lượng giữ tải dài "
                   "hơn nhiều (vd `2h`).")
    st.info("Mức mục tiêu (số VU) do người dùng đặt – nên lấy từ yêu cầu/cam kết của dự án, số liệu vận hành giờ cao "
            "điểm, hoặc ước tính: tổng tài khoản × % online × % đang thao tác. Các bậc Stress (50/100/125/150%) là quy "
            "ước cố định của ứng dụng.")
    st.caption("JMeter chạy cùng hồ sơ tải với k6: mỗi bậc tăng tải là 1 Thread Group có độ trễ khởi động (không cần "
               "plugin). Khác biệt duy nhất: JMeter giảm tải tức thời ở đầu giai đoạn ramp-down, k6 giảm dần.")
    st.markdown("""
#### Smoke Test lỗi nhưng Load Test / Stress Test lại không lỗi?
**Không được bỏ qua** – Smoke Test lỗi là tín hiệu cần giải thích trước khi tin kết quả của các kịch bản sau.
Các nguyên nhân thường gặp:
- **Mẫu nhỏ**: Smoke Test ít request nên vài lỗi đã thành tỷ lệ % lớn, và p95 của ít mẫu bị 1–2 request chậm kéo lên.
  Cùng số lỗi đó trong Load Test (hàng chục nghìn request) chỉ còn rất nhỏ → *Đạt*.
- **Khởi động nguội (cold start)**: Smoke Test thường chạy đầu tiên, gặp lúc máy chủ chưa nạp cache, chưa mở đủ
  kết nối CSDL → các request đầu chậm/lỗi. Kịch bản sau chạy khi hệ thống đã "nóng".
- **Sự cố tạm thời** của môi trường lúc chạy Smoke Test (đang deploy, mạng chập chờn, phiên đăng nhập hết hạn…).
- **Lỗi kịch bản/dữ liệu**: một bước chỉ lỗi trong điều kiện nhất định (dữ liệu thiếu, token, thứ tự thao tác).

**Cách xử lý:** xem mã lỗi và thời điểm lỗi ở bước 8 (lỗi dồn ở đầu phiên → cold start; rải đều → lỗi thật) → sửa
nguyên nhân rồi **chạy lại Smoke Test** cho tới khi sạch lỗi → mới tin kết quả Load/Stress Test. Báo cáo ghi trung
thực cả hai kết quả; mục tổng quan tự nhận diện và phân tích trường hợp này.
""")


def accounts_panel(p: Project, pool: list[tuple[str, str]]) -> None:
    """Tóm tắt tài khoản sẽ dùng và cách chia cho các VU."""
    cfg = p.test_config
    if cfg.auth_mode != "login_per_vu":
        if len(p.login.extra_accounts):
            st.caption("ℹ️ Chế độ xác thực hiện tại không đăng nhập theo từng VU nên danh sách nhiều tài khoản không được dùng.")
        return
    n, vus = len(pool), max(1, cfg.vus)
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 1, 3])
        c1.metric("Tài khoản dùng", n)
        c2.metric("VU / tài khoản", f"≈{vus / max(n, 1):.1f}")
        if n <= 1:
            c3.info("Mọi VU dùng chung 1 tài khoản. Nếu hệ thống giới hạn tần suất theo tài khoản (HTTP 429), hãy thêm "
                    "tài khoản ở bước 1 và chọn *Chia đều … tài khoản*.")
        else:
            last = min(n, vus)          # chỉ liệt kê tới VU cuối cùng thực sự có
            c3.markdown(f"VU 1 → **{pool[0][0]}**"
                        + (f", VU 2 → **{pool[1][0]}**" if last >= 2 else "")
                        + (f", {'…, ' if last > 3 else ''}VU {last} → **{pool[last - 1][0]}**" if last > 2 else "")
                        + (f", VU {n + 1} quay lại **{pool[0][0]}** …" if vus > n else "")
                        + (f"  \n⚠️ Số tài khoản ({n}) nhiều hơn số VU ({vus}) – chỉ {vus} tài khoản đầu được dùng."
                           if n > vus else ""))
        miss = [u for u, pw in pool if not pw]
        if miss:
            st.warning(f"{len(miss)} tài khoản chưa có mật khẩu (có thể do khởi động lại ứng dụng mà chưa tick ghi nhớ): "
                       f"{', '.join(miss[:8])}{'…' if len(miss) > 8 else ''}. Nhập lại ở bước 1.")


def _profile_df(p: Project) -> pd.DataFrame:
    t, pts = 0, [(0, 0)]
    for s in stages_for(p.test_config):
        t += s.duration_s
        pts.append((t, s.target))
    return pd.DataFrame(pts, columns=["Giây", "VUs"]).set_index("Giây")


def _generate_scripts(p: Project, pool: list) -> int:
    """Sinh lại script cho mọi lượt chờ chạy theo cấu hình hiện tại (bỏ các lượt chờ cũ). Trả về số script."""
    cfg = p.test_config
    p.runs = [r for r in p.runs if r.status != "pending"]
    cfg.accounts_count = len(pool) if cfg.auth_mode == "login_per_vu" else 1
    new = plan_runs(p)
    for r in new:
        save_run_file(r)
    p.runs.extend(new)
    return len(new)


def _stale(r, cfg) -> bool:
    """Lượt chờ chạy có script sinh theo cấu hình khác cấu hình hiện tại (bỏ qua accounts_count – tự đặt khi sinh)."""
    if not r.config:
        return False
    cur = cfg.model_dump()
    return any(r.config.get(k) != v for k, v in cur.items() if k != "accounts_count" and k in r.config)


def render(p: Project) -> None:
    st.header("7. Cấu hình & chạy kiểm thử")
    if not p.confirmed:
        st.warning("Cần xác nhận phạm vi kiểm thử ở bước 5 trước khi cấu hình và chạy test.")
        sync_runs(p)
        if p.runs:           # vẫn cho xem lịch sử các lượt đã chạy trước khi huỷ xác nhận
            job_panel(p.id, JOB, "Tiến trình chạy test", height=320)
            st.subheader("Lịch sử lượt chạy")
            run_history(p)
        state.nav_buttons("confirm", "results", next_disabled=not any(r.status == "done" for r in p.runs))
        return
    sync_runs(p)
    cfg = p.test_config
    running = jobs.is_running(p.id, JOB)

    # ---------------------------------------------------------------- cấu hình
    h1, h2 = st.columns([3, 1], vertical_alignment="bottom")
    sec = section_titles(7)
    sec("Cấu hình tải", h1)
    with h2.popover("❔ Hướng dẫn", width="stretch", help="Giải thích 5 loại kịch bản và cách ứng dụng tạo tải"):
        scenario_guide(cfg)
    with st.form("cfg_form"):
        c1, c2, c3, c4 = st.columns(4)
        types = list(SCENARIO_TYPES)
        stype = c1.selectbox("Loại kịch bản", types, index=types.index(cfg.scenario_type), format_func=scenario_name)
        vus = c2.number_input("Số người dùng ảo (VUs)", 1, 100000, cfg.vus, 10)
        ramp = c3.text_input("Ramp-up", cfg.ramp_up, help="VD: 30s, 1m, 2m30s")
        dur = c4.text_input("Thời lượng giữ tải", cfg.duration, help="VD: 5m, 1h")
        c1, c2, c3, c4 = st.columns(4)
        rdown = c1.text_input("Ramp-down", cfg.ramp_down)
        think = c2.number_input("Think time (giây)", 0.0, 60.0, float(cfg.think_time_s), 0.5)
        p95 = c3.number_input("Ngưỡng p95 (ms)", 50, 600000, int(cfg.p95_threshold_ms), 100)
        err = c4.number_input("Ngưỡng tỷ lệ lỗi (%)", 0.0, 100.0, cfg.error_rate_threshold * 100, 0.5)
        c1, c2, c3 = st.columns([2, 2, 1])
        auth = c1.selectbox("Xác thực trong script", list(AUTH_MODES), index=list(AUTH_MODES).index(cfg.auth_mode),
                            format_func=AUTH_MODES.get)
        mode = c2.selectbox("Chế độ chạy", ["sequential", "combined"], index=["sequential", "combined"].index(cfg.run_mode),
                            format_func=lambda m: "Tuần tự từng UC (mỗi UC 1 lượt)" if m == "sequential"
                            else "Gộp các UC chạy đồng thời (chia đều VUs)")
        timeout = c3.number_input("HTTP timeout (s)", 5, 600, cfg.http_timeout_s)
        n_pool = len(state.test_accounts(p, mode="multi"))
        acc_mode = st.radio("Tài khoản dùng khi chạy test", ["single", "multi"],
                            index=["single", "multi"].index(cfg.account_mode), horizontal=True,
                            format_func=lambda m: f"Chỉ tài khoản chính ({p.login.username or '—'}) cho mọi VU"
                            if m == "single" else f"Chia đều {n_pool} tài khoản cho các VU (khai báo ở bước 1)")
        st.caption(SCENARIO_TYPES[stype])
        if st.form_submit_button("💾 Lưu cấu hình"):
            # thời lượng giữ tải phải > 0 (k6 báo lỗi "duration must be > 0"); ramp-up/ramp-down được phép = 0
            bad = [n for n, v, z in (("Ramp-up", ramp, True), ("Thời lượng", dur, False), ("Ramp-down", rdown, True))
                   if not valid_duration(v, allow_zero=z)]
            if bad:
                st.error(f"Định dạng thời gian không hợp lệ: {', '.join(bad)} – dùng số + đơn vị h/m/s/ms "
                         "(vd 30s, 5m, 1h30m, 2m30s); thời lượng giữ tải phải lớn hơn 0.")
            else:
                ramp, dur, rdown = ramp.strip(), dur.strip(), rdown.strip()
                cfg.scenario_type, cfg.vus, cfg.ramp_up, cfg.duration, cfg.ramp_down = stype, int(vus), ramp, dur, rdown
                cfg.think_time_s, cfg.p95_threshold_ms, cfg.error_rate_threshold = think, float(p95), err / 100
                cfg.auth_mode, cfg.run_mode, cfg.http_timeout_s = auth, mode, int(timeout)
                cfg.account_mode = acc_mode
                n_pend = sum(1 for r in p.runs if r.status == "pending")
                if n_pend:      # script đã sinh theo cấu hình cũ -> sinh lại ngay để lượt chờ chạy dùng đúng cấu hình mới
                    n = _generate_scripts(p, state.test_accounts(p))
                    st.session_state[f"cfg_msg_{p.id}"] = f"Đã lưu cấu hình và sinh lại {n} script theo cấu hình mới."
                state.save(p)
                st.rerun()
    msg = st.session_state.pop(f"cfg_msg_{p.id}", None)
    if msg:
        st.success(msg)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.caption("Hồ sơ tải (k6 và JMeter cùng hồ sơ; JMeter giảm tải tức thời ở ramp-down)")
        st.area_chart(_profile_df(p), height=180, color="#D9822B")
    n_runs = len(cfg.tools) * (len(p.scenarios) if cfg.run_mode == "sequential" else 1)
    est = total_seconds(cfg) * n_runs
    c2.metric("Số lượt chạy", n_runs)
    c2.metric("Thời gian ước tính", fmt_duration(est))
    if p.auth.login_url == "" and cfg.auth_mode == "login_per_vu":
        st.info("Chưa bắt được API đăng nhập ở bước 3 → script sẽ tự dùng cookie/token phiên đã ghi nhận.")
    if cfg.scenario_type == "smoke" and cfg.vus > 10:
        st.info(f"Smoke Test thường chỉ dùng 3–10 VU để kiểm tra script chạy đúng; đang đặt {cfg.vus} VU. "
                "Nên giảm số VU ở 7a trước khi chạy Smoke Test.")
    if cfg.run_mode == "combined" and 1 < len(p.scenarios) and cfg.vus < len(p.scenarios):
        st.warning(f"Chế độ gộp chia VU cho {len(p.scenarios)} UC nhưng chỉ đặt {cfg.vus} VU → mỗi UC vẫn cần tối thiểu "
                   f"1 VU nên thực tế sẽ chạy {len(p.scenarios)} VU (nhiều hơn số đã cấu hình).")
    pool = state.test_accounts(p)
    accounts_panel(p, pool)

    # ---------------------------------------------------------------- sinh script
    sec("Script kiểm thử")
    pending = [r for r in p.runs if r.status == "pending"]
    b1, b2, _ = st.columns([1, 1, 2])
    if b1.button("📝 Sinh script", disabled=running, width="stretch"):
        n = _generate_scripts(p, pool)
        state.save(p)
        st.success(f"Đã sinh {n} script.")
        st.rerun()
    stale = [r for r in pending if _stale(r, cfg)]
    if stale:
        st.warning(f"{len(stale)} script chờ chạy được sinh theo cấu hình CŨ (khác cấu hình 7a hiện tại, vd số VU). "
                   "Bấm **📝 Sinh script** để sinh lại trước khi chạy.")
    if b2.button(f"▶ Chạy test ({len(pending)} lượt)", type="primary", disabled=running or not pending or bool(stale),
                 width="stretch"):
        missing = [t for t in {r.tool for r in pending}
                   if (t == "k6" and not config.k6_executable()) or (t == "jmeter" and not config.jmeter_executable())]
        no_pwd = [u for u, pw in pool if not pw]
        if missing:
            st.error(f"Chưa cài: {', '.join(missing)}")
        elif no_pwd and cfg.auth_mode == "login_per_vu":
            st.error(f"Các tài khoản chưa có mật khẩu: {', '.join(no_pwd[:10])} – nhập lại ở bước 1.")
        else:
            env = {"PERFTOOL_ACCOUNTS": json.dumps(pool, ensure_ascii=False)}
            if p.monitor.enabled:
                from .monitor_panel import monitor_secrets
                env["PERFTOOL_MONITOR_SECRETS"] = json.dumps(monitor_secrets(p), ensure_ascii=False)
            jobs.start_job(p.id, JOB, "perftool.runner.cli",
                           ["--project", p.id, "--runs", ",".join(r.run_id for r in pending)], env=env)
            st.rerun()
    _monitor_notice(p)

    if pending:
        tabs = st.tabs([f"{r.tool} · {r.uc_code}" for r in pending])
        for tab, r in zip(tabs, pending):
            with tab:
                f = Path(r.script_file)
                txt = f.read_text(encoding="utf-8") if f.exists() else ""
                new_txt = st.text_area("Nội dung script (có thể chỉnh sửa)", txt, height=360, key=f"scr_{r.run_id}")
                d1, d2, _ = st.columns([1, 1, 3])
                if d1.button("💾 Lưu script", key=f"savescr_{r.run_id}") and new_txt != txt:
                    f.write_text(new_txt, encoding="utf-8")
                    st.success("Đã lưu.")
                d2.download_button("⬇ Tải về", txt, file_name=f.name, key=f"dl_{r.run_id}")
                if (r.config or {}).get("auth_mode") == "static_headers":
                    st.warning("Script này chứa **cookie/token phiên đăng nhập thật** (chế độ dùng cookie/token đã ghi "
                               "nhận): ai có file đều dùng được phiên của tài khoản cho tới khi phiên hết hạn – không "
                               "gửi file qua email/chat; xoá sau khi dùng.")
                st.caption(f"`{f}`")

    # ---------------------------------------------------------------- theo dõi
    sec("Theo dõi tiến trình chạy test")
    job_panel(p.id, JOB, "Tiến trình chạy test", height=320)
    if p.runs:
        sec("Lịch sử lượt chạy")
        run_history(p)
        with st.expander("🗑 Dọn lịch sử"):
            clean_history(p)

    state.nav_buttons("tool", "results", next_disabled=not any(r.status == "done" for r in p.runs))


def _monitor_notice(p: Project) -> None:
    """Cho biết lượt chạy có kèm thu số liệu tài nguyên máy chủ (cấu hình ở bước 6) hay không."""
    if not p.monitor.enabled:
        st.caption("📡 Thu số liệu tài nguyên máy chủ: **đang tắt** (tuỳ chọn – bật ở Bước 6 trước khi chạy test).")
        return
    from .monitor_panel import missing_secrets, summary_text
    st.caption(f"📡 Thu số liệu tài nguyên máy chủ khi chạy test: **bật** – {summary_text(p)} (cấu hình ở Bước 6).")
    from ...monitor.collector import has_live
    m = p.monitor
    if not has_live(m) and not ("zabbix" in m.tools and m.zabbix_url) and not ("prometheus" in m.tools and m.prom_url):
        st.warning("Chưa có máy chủ nào thu được số liệu trong lúc chạy test (cần máy `local`, hoặc máy `ssh` có địa chỉ IP, "
                   "hoặc Zabbix/Prometheus) – lượt chạy sẽ không có số liệu tài nguyên. Kiểm tra lại ở Bước 6.")
    miss = missing_secrets(p)
    if miss:
        st.warning(f"Chưa có mật khẩu cho: {', '.join(miss)} – các nguồn này sẽ không thu được số liệu. "
                   "Nhập ở Bước 6 → Lưu & kiểm tra kết nối.")
