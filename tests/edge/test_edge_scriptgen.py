"""4. scriptgen / profile: 5 loại kịch bản với VUs 1, 0, rất lớn; scenario_name; escape ký tự đặc biệt trong k6 JS
(kiểm tra cú pháp bằng `node --check`) và JMeter JMX (parse XML)."""
from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

import re

import pytest

from perftool.models import AuthCapture, Project, ScenarioStep, TestScenario
from perftool.scriptgen.generator import generate_jmeter, generate_k6
from perftool.scriptgen.profile import (SCENARIO_TYPES, parse_duration, scenario_name, scenario_rank, split_vus,
                                        stages_for, total_seconds)

NODE = shutil.which("node")
TYPES = list(SCENARIO_TYPES)


def _cfg(typ, vus, up="1m", hold="5m", down="30s"):
    p = Project(id="x")
    c = p.test_config
    c.scenario_type, c.vus, c.ramp_up, c.duration, c.ramp_down = typ, vus, up, hold, down
    return c


# ------------------------------------------------------------------ hồ sơ tải
@pytest.mark.parametrize("typ", TYPES)
@pytest.mark.parametrize("vus", [1, 0, 100_000])
def test_stages_all_types_and_vus(typ, vus):
    st = stages_for(_cfg(typ, vus))
    assert st and all(s.duration_s >= 0 and s.target >= 0 for s in st)
    peak = max(s.target for s in st)
    eff = max(1, vus)
    if typ == "stress":
        assert peak == int(eff * 1.5) or (eff == 1 and peak == 1)
    else:
        assert peak == eff
    if typ != "smoke":
        assert st[-1].target == 0                      # luôn kết thúc bằng ramp-down về 0
    assert total_seconds(_cfg(typ, vus)) == sum(s.duration_s for s in st)


def test_stages_explicit_vus_override_and_zero_fallback():
    c = _cfg("load", 20)
    assert max(s.target for s in stages_for(c, 7)) == 7
    assert max(s.target for s in stages_for(c, 0)) == 20       # 0 -> dùng cfg.vus


def test_scenario_name_and_rank_every_type():
    assert {k: scenario_name(k) for k in TYPES} == {"smoke": "Smoke Test", "load": "Load Test",
                                                    "stress": "Stress Test", "spike": "Spike Test",
                                                    "soak": "Soak Test"}
    assert scenario_name("") == "" and scenario_rank("unknown") == 9
    assert [scenario_rank(k) for k in TYPES] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("n,total", [(1, 1), (3, 1), (3, 0), (4, 10), (7, 100000)])
def test_split_vus_never_zero(n, total):
    parts = split_vus(total, n)
    assert len(parts) == n and min(parts) >= 1 and sum(parts) == max(total, n)


@pytest.mark.parametrize("s,sec", [("", 0), ("abc", 0), ("1.5m", 90), ("1h 30m", 5400), ("0", 0), (45, 45),
                                   ("2M30S", 150)])
def test_parse_duration_edges(s, sec):
    assert parse_duration(s) == sec


def test_parse_duration_milliseconds_not_minutes():
    """[Hồi quy – lỗi đã sửa 2026-09-25] '500ms' (cú pháp k6 hợp lệ) bị hiểu là 500 phút = 30000 s; UI chỉ báo lỗi khi kết quả = 0."""
    assert parse_duration("500ms") < 60


# ------------------------------------------------------------------ sinh script với ký tự đặc biệt
NASTY = "Tìm kiếm <b>&\"'`${x}\\ </script> -- ; }"


def _project(code="UC-1", name=NASTY, proj_name="Dự án \"A\" & <B>", url_q="a=1&b=<2>&c=\"x\"&d='y'"):
    p = Project(id="gen")
    p.info.name = proj_name
    p.login.base_url = "https://app.example.com"
    p.auth = AuthCapture(login_url="https://app.example.com/api/login?x=1&y=2", login_content_type="application/json",
                         login_body_template='{"u":"{{USERNAME}}","p":"{{PASSWORD}}","n":"<&>"}',
                         token_json_path="data.token", static_headers={"X-A": "v<&>\"'"})
    p.scenarios = [TestScenario(uc_code=code, uc_name=name, steps=[
        ScenarioStep(name=f"Bước {NASTY}", url=f"https://app.example.com/api/tìm-kiếm?{url_q}",
                     method="POST", body='{"q":"</script><!-- \' \\" ${a}"}', content_type="application/json",
                     headers={"X-Weird": "a\"b<c>&d"}),
        ScenarioStep(name="write", method="DELETE", url="https://app.example.com/api/x/1", enabled=False),
    ])]
    return p


def _node_check(js_file):
    if not NODE:
        pytest.skip("không có node để kiểm tra cú pháp JS")
    mjs = js_file.with_suffix(".mjs")
    mjs.write_text(js_file.read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(mjs)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr[-800:]


def test_k6_special_chars_valid_js_and_disabled_steps(tmp_path):
    p = _project()
    js = generate_k6(p, p.scenarios, tmp_path / "s.js")
    _node_check(js)
    txt = js.read_text(encoding="utf-8")
    assert "/api/x/1" not in txt                       # bước ghi đã tắt không được sinh


def test_jmx_special_chars_valid_xml_and_roundtrip(tmp_path):
    p = _project()
    root = ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx")).getroot()
    names = [e.get("testname") for e in root.iter("HTTPSamplerProxy")]
    # chuỗi có '${' được escape '\' -> '\\', '$' -> '\$' (JMeter bỏ escape khi thay biến -> hiển thị đúng chuỗi gốc,
    # không thực thi ${x}); so sánh sau khi bỏ escape
    unesc = lambda t: re.sub(r"\\(.)", r"\1", t or "")  # noqa: E731
    assert f"UC-1 :: Bước {NASTY}" in [unesc(n) for n in names]
    tg = next(root.iter("ThreadGroup"))
    assert unesc(tg.get("testname")) == f"UC-1 - {NASTY}"
    paths = [e.text for e in root.iter("stringProp") if e.get("name") == "HTTPSampler.path"]
    assert "/api/tìm-kiếm?a=1&b=<2>&c=\"x\"&d='y'" in paths
    assert not any(e.text and "/api/x/1" in e.text for e in root.iter("stringProp"))


@pytest.mark.parametrize("typ", TYPES)
@pytest.mark.parametrize("vus", [1, 100_000])
def test_generate_all_types_valid(tmp_path, typ, vus):
    p = _project(name="UC thường")
    p.test_config.scenario_type, p.test_config.vus = typ, vus
    ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx"))
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert scenario_name(typ) in js


def test_k6_multiline_uc_name_from_excel(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Tên UC nhập từ Excel thường có xuống dòng trong ô. k6 template chèn uc_name thô vào dòng chú thích `// ...`
    -> phần sau dấu xuống dòng thành mã JS -> script k6 lỗi cú pháp."""
    p = _project(name="Thống kê giao việc\ntheo phòng ban (xem, lọc)")
    _node_check(generate_k6(p, p.scenarios, tmp_path / "s.js"))


def test_k6_multiline_project_name(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Tương tự với tên dự án (dòng chú thích đầu file)."""
    p = _project(name="x", proj_name="Hệ thống A\nGiai đoạn 2")
    _node_check(generate_k6(p, p.scenarios, tmp_path / "s.js"))


def test_k6_uc_code_with_quote(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] uc_code chèn thô trong chuỗi nháy đơn JS ('{{ sc.uc_code }}') -> mã UC có dấu ' làm hỏng script."""
    p = _project(code="UC-1'A", name="x")
    _node_check(generate_k6(p, p.scenarios, tmp_path / "s.js"))


def test_k6_codes_colliding_after_safe_key(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] 'UC-1' và 'UC.1' (hoặc 2 UC trùng mã) cùng thành key/tên hàm uc_UC_1 -> `export function` trùng tên
    -> k6 báo SyntaxError, cả lượt chạy gộp (combined) thất bại."""
    p = _project(name="x")
    sc2 = p.scenarios[0].model_copy(update={"uc_code": "UC.1", "uc_name": "y"})
    _node_check(generate_k6(p, [p.scenarios[0], sc2], tmp_path / "s.js"))


def test_jmx_project_name_with_double_hyphen(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Tên dự án được chèn vào chú thích XML <!-- ... -->; '--' trong chú thích là XML không hợp lệ."""
    p = _project(name="x", proj_name="Hệ thống A -- đợt 2")
    ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx"))


def test_jmx_control_characters_from_excel(tmp_path):
    """[Hồi quy – lỗi đã sửa 2026-09-25] Ký tự điều khiển (vd \\x0b - xuống dòng mềm khi dán từ Word vào Excel) không hợp lệ trong XML 1.0."""
    p = _project(name="Tên\x0bUC")
    ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx"))


def test_jmx_vus_zero_single_scenario(tmp_path):
    """VUs = 0 (UI chặn min=1): k6 dùng tối thiểu 1 VU; ghi nhận hành vi JMeter để so sánh."""
    p = _project(name="x")
    p.test_config.vus = 0
    root = ET.parse(generate_jmeter(p, p.scenarios, tmp_path / "p.jmx")).getroot()
    n = [e.text for e in root.iter("stringProp") if e.get("name") == "ThreadGroup.num_threads"][0]
    js = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    assert "target: 1" in js
    assert n in ("0", "1")


def test_k6_steps_json_roundtrip(tmp_path):
    p = _project()
    txt = generate_k6(p, p.scenarios, tmp_path / "s.js").read_text(encoding="utf-8")
    start = txt.index("const STEPS_1 = ") + len("const STEPS_1 = ")
    end = txt.index(";\n", start)
    steps = json.loads(txt[start:end])
    assert steps[0]["name"] == f"Bước {NASTY}" and steps[0]["headers"] == {"X-Weird": "a\"b<c>&d"}
    assert len(steps) == 1
