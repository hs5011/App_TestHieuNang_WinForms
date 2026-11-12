"""Bước 6 - Chọn công cụ kiểm thử (k6 / JMeter / cả hai)."""
from __future__ import annotations

import re
import subprocess

import streamlit as st

from ... import config
from ...models import Project
from .. import state


@st.cache_data(ttl=300, show_spinner=False)
def _version(tool: str) -> tuple[str | None, str]:
    exe = config.k6_executable() if tool == "k6" else config.jmeter_executable()
    if not exe:
        return None, ""
    try:
        args = [exe, "version"] if tool == "k6" else [exe, "--version"]
        out = subprocess.run(args, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if tool == "k6":
            line = next((ln.strip() for ln in out.splitlines() if "k6" in ln), out[:80])
        else:
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)\s*$", out, re.MULTILINE)
            line = f"Apache JMeter {m.group(1)}" if m else "Apache JMeter"
        return exe, line
    except Exception as e:  # noqa: BLE001
        return exe, f"(không lấy được phiên bản: {e})"


def render(p: Project) -> None:
    st.header("6. Chọn công cụ kiểm thử hiệu năng")
    if not p.confirmed:
        st.warning("Cần xác nhận phạm vi kiểm thử ở bước 5 trước.")
        state.nav_buttons("confirm", None)
        return

    st.session_state[f"visited_tool_{p.id}"] = True      # để thanh bên đánh dấu ✅ bước 6
    st.subheader("6a. Công cụ kiểm thử (k6 / JMeter)")
    k6_exe, k6_ver = _version("k6")
    jm_exe, jm_ver = _version("jmeter")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**k6**")
        st.caption("Script JavaScript, nhẹ, phù hợp tải lớn trên 1 máy; hỗ trợ ramping stages, threshold.")
        if k6_exe:
            st.success(f"Đã cài: {k6_ver}\n\n`{k6_exe}`")
        else:
            st.error("Chưa cài k6.")
            st.code("winget install k6 --source winget", language="bash")
    with c2.container(border=True):
        st.markdown("**Apache JMeter**")
        st.caption("Kế hoạch .jmx mở được bằng JMeter GUI, có HTML dashboard; cần Java.")
        if jm_exe:
            st.success(f"Đã cài: {jm_ver}\n\n`{jm_exe}`")
        else:
            st.error("Chưa tìm thấy JMeter. Khai báo `tools.jmeter_path` trong config/settings.yaml.")

    choice_map = {"k6": ["k6"], "JMeter": ["jmeter"], "Cả hai (k6 + JMeter)": ["k6", "jmeter"]}
    rev = {tuple(v): k for k, v in choice_map.items()}
    cur = rev.get(tuple(p.test_config.tools), "k6")
    choice = st.radio("Công cụ sử dụng", list(choice_map), index=list(choice_map).index(cur), horizontal=True)
    tools = choice_map[choice]
    missing = [t for t in tools if (t == "k6" and not k6_exe) or (t == "jmeter" and not jm_exe)]
    if missing:
        st.warning(f"Công cụ chưa sẵn sàng: {', '.join(missing)} – vẫn có thể sinh script nhưng không chạy được.")
    if tools != p.test_config.tools:
        p.test_config.tools = tools
        state.save(p)
    if st.button("🔄 Dò lại công cụ"):
        _version.clear()
        config.reload()
        st.rerun()

    from .monitor_panel import render_config
    render_config(p, "6b")
    state.nav_buttons("confirm", "run")
