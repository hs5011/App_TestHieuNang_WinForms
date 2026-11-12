"""Sinh script k6 (.js) và kế hoạch JMeter (.jmx) từ kịch bản đã xác nhận."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import escape

from .. import config
from ..config import TEMPLATES_DIR
from ..models import AuthCapture, Project, TestScenario
from .profile import jmeter_blocks, scenario_name, split_vus, stages_for

_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined,
                   trim_blocks=False, lstrip_blocks=False, keep_trailing_newline=True)

# ký tự không hợp lệ trong XML 1.0 (vd \x0b khi dán từ Word sang Excel) -> JMX không mở được
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _oneline(v) -> str:
    """Gom xuống dòng/tab thành 1 khoảng trắng (tên UC có Alt+Enter trong Excel làm vỡ dòng chú thích // của k6)."""
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _xml(v) -> str:
    """Escape XML + bỏ ký tự điều khiển không hợp lệ."""
    return str(escape(_XML_BAD.sub("", str(v if v is not None else ""))))


def _jm_escape(s: str, force: bool = False) -> str:
    """Vô hiệu hoá hàm/biến JMeter (`${__groovy(...)}`, `${VAR}`) trong dữ liệu bắt được từ trang đích.

    JMeter chỉ phân tích chuỗi có chứa '${'; khi phân tích, '\\\\' -> '\\', '\\$' -> '$'. Vì vậy escape '\\' và '$'
    cho ra đúng chuỗi gốc mà không thực thi gì. force=True: chuỗi sẽ được ghép với biến của PerfTool (${USERNAME},
    ${AUTH_TOKEN}…) nên chắc chắn bị phân tích -> luôn escape.
    """
    if not force and "${" not in s:
        return s
    return s.replace("\\", "\\\\").replace("$", "\\$")


def _jx(v) -> str:
    """Escape cho JMX: vô hiệu hoá ${…} của JMeter + escape XML."""
    return _xml(_jm_escape(_XML_BAD.sub("", str(v if v is not None else ""))))


def _jxf(v) -> str:
    """Như _jx nhưng luôn escape (phần dữ liệu đứng cạnh biến ${…} của PerfTool trong cùng 1 thuộc tính)."""
    return _xml(_jm_escape(_XML_BAD.sub("", str(v if v is not None else "")), force=True))


_IDENT_BAD = re.compile(r"[^A-Za-z0-9_]")


def _ident(v) -> str:
    """Tên biến JMeter/k6 an toàn (chỉ chữ, số, _)."""
    return _IDENT_BAD.sub("_", str(v or ""))


def _xml_comment(v) -> str:
    """Chuỗi an toàn trong chú thích <!-- -->: 1 dòng, không chứa '--'."""
    s = _XML_BAD.sub("", _oneline(v))
    while "--" in s:
        s = s.replace("--", "- -")
    return s.rstrip("-")


_env.filters.update(oneline=_oneline, x=_xml, xc=_xml_comment, jx=_jx, jxf=_jxf)


def _url_parts(url: str) -> dict:
    u = urlparse(url)
    port = u.port or ""
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return {"protocol": u.scheme or "https", "host": u.hostname or "", "port": port, "path": path}


# URL trang đăng nhập (giống LOGIN_PAGE_RE của script k6): bước nghiệp vụ bị chuyển về đây = mất phiên -> lỗi
LOGIN_PAGE_RE = re.compile(r"(login|dang-nhap|signin|sign-in|auth)", re.I)
LOGIN_PAGE_JAVA_RE = "(?i)(login|dang-nhap|signin|sign-in|auth)"

# escape JSON giá trị biến JMeter (bỏ 2 dấu " ngoài cùng); không chứa dấu phẩy nên không cần escape tham số hàm
_JM_JSON_ESC = "${{__groovy(groovy.json.JsonOutput.toJson(vars.get('{}') ?: '').drop(1).dropRight(1))}}"


def _login_ctx(auth: AuthCapture) -> dict:
    ctype = auth.login_content_type or ""
    urlenc = "x-www-form-urlencoded" in ctype
    body = auth.login_body_template or ""
    # body gốc lấy từ trang đích -> escape ${…} TRƯỚC khi chèn biến của PerfTool (template dùng |x, không |jx)
    safe = _jm_escape(_XML_BAD.sub("", body), force=True)
    if urlenc:
        jbody = safe.replace("{{USERNAME}}", "${__urlencode(${USERNAME})}").replace(
            "{{PASSWORD}}", "${__urlencode(${PASSWORD})}")
    elif "json" in ctype or body.lstrip()[:1] in ("{", "["):
        # mật khẩu có " hoặc \ phá cú pháp JSON -> escape như JSON.stringify của k6
        jbody = safe.replace("{{USERNAME}}", _JM_JSON_ESC.format("USERNAME")).replace(
            "{{PASSWORD}}", _JM_JSON_ESC.format("PASSWORD"))
    else:
        jbody = safe.replace("{{USERNAME}}", "${USERNAME}").replace("{{PASSWORD}}", "${PASSWORD}")
    return {
        "url": auth.login_url, "method": auth.login_method or "POST", "body": body, "content_type": ctype,
        "urlencoded": urlenc, "json": "json" in ctype,
        "token_path": auth.token_json_path, "token_header": auth.token_header or "Authorization",
        "token_prefix": auth.token_prefix if auth.token_prefix is not None else "Bearer ",
        "jmeter_body": jbody, "u": _url_parts(auth.login_url) if auth.login_url else {},
    }


def _cookies(auth: AuthCapture, base_url: str) -> list[dict]:
    out = []
    for c in auth.cookies:
        dom = (c.get("domain") or urlparse(base_url).hostname or "").lstrip(".")
        out.append({"name": c.get("name", ""), "value": c.get("value", ""), "domain": dom,
                    "path": c.get("path", "/"), "secure": bool(c.get("secure")),
                    "url": f"{'https' if c.get('secure') else urlparse(base_url).scheme}://{dom}{c.get('path', '/')}"})
    return out


def _steps(sc: TestScenario) -> list[dict]:
    return [{"name": s.name, "method": re.sub(r"[^A-Z]", "", (s.method or "GET").upper()) or "GET",
             "url": s.url, "body": s.body or "",
             "content_type": s.content_type or "", "think": float(s.think_time_s or 0),
             "think_ms": int(float(s.think_time_s or 0) * 1000), "u": _url_parts(s.url),
             "extract": s.extract_token or "", "var": _ident(s.token_var), "use": _ident(s.use_token),
             "login_guard": not LOGIN_PAGE_RE.search(s.url or ""),
             "headers": dict(s.headers or {})}
            for s in sc.steps if s.enabled]


def _safe_key(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", code)


def _unique_keys(codes: list[str]) -> list[str]:
    """Khoá JS duy nhất cho từng UC: 'UC-1' và 'UC.1' (hoặc 2 UC trùng mã) không được sinh 2 hàm cùng tên."""
    out, used = [], set()
    for c in codes:
        base = _safe_key(c) or "UC"
        k, i = base, 2
        while k in used:
            k, i = f"{base}_{i}", i + 1
        used.add(k)
        out.append(k)
    return out


def _common(project: Project, scenarios: list[TestScenario], file_name: str) -> dict:
    cfg = project.test_config
    auth_mode = cfg.auth_mode
    if auth_mode == "login_per_vu" and not project.auth.login_url:
        auth_mode = "static_headers"   # không bắt được API đăng nhập -> dùng header/cookie tĩnh
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "project_name": project.info.name,
        "scenario_type": cfg.scenario_type, "scenario_name": scenario_name(cfg.scenario_type), "vus": cfg.vus, "duration": cfg.duration, "file_name": file_name,
        # token/cookie phiên chỉ nhúng vào script khi thật sự dùng chế độ header/cookie tĩnh (tránh lộ phiên đăng nhập)
        "auth_mode": auth_mode, "static_headers": project.auth.static_headers if auth_mode == "static_headers" else {},
        "cookies": _cookies(project.auth, project.login.base_url) if auth_mode == "static_headers" else [],
        "insecure_tls": config.insecure_tls(project.login),
        "login": _login_ctx(project.auth),
        "http_timeout": cfg.http_timeout_s, "think_time": cfg.think_time_s, "think_ms": int(cfg.think_time_s * 1000),
        "p95_threshold": cfg.p95_threshold_ms, "error_rate_threshold": cfg.error_rate_threshold,
        "multi_accounts": cfg.account_mode == "multi" and auth_mode == "login_per_vu",
        "login_page_re": LOGIN_PAGE_JAVA_RE,
    }


def generate_k6(project: Project, scenarios: list[TestScenario], out_file: Path) -> Path:
    cfg = project.test_config
    vus_list = split_vus(cfg.vus, len(scenarios)) if len(scenarios) > 1 else [cfg.vus]
    ctx = _common(project, scenarios, out_file.name)
    keys = _unique_keys([sc.uc_code for sc in scenarios])
    ctx["scenarios"] = [{
        "key": k, "fn": f"uc_{k}", "uc_code": sc.uc_code,
        "uc_name": sc.uc_name, "stages": stages_for(cfg, v), "steps": _steps(sc),
        "p95_threshold": sc.p95_threshold_ms or cfg.p95_threshold_ms,
    } for sc, v, k in zip(scenarios, vus_list, keys)]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(_env.get_template("k6_script.js.j2").render(**ctx), encoding="utf-8")
    return out_file


def generate_jmeter(project: Project, scenarios: list[TestScenario], out_file: Path) -> Path:
    cfg = project.test_config
    vus_list = split_vus(cfg.vus, len(scenarios)) if len(scenarios) > 1 else [cfg.vus]
    ctx = _common(project, scenarios, out_file.name)
    # cùng hồ sơ tải với k6 (Stress theo bậc 50→150%, Spike…): mỗi bậc tăng tải = 1 Thread Group có độ trễ khởi động
    ctx["scenarios"] = [{
        "uc_code": sc.uc_code, "uc_name": sc.uc_name, "vus": max(int(v), 1), "steps": _steps(sc),
        "groups": jmeter_blocks(stages_for(cfg, v)),
    } for sc, v in zip(scenarios, vus_list)]
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(_env.get_template("jmeter_plan.jmx.j2").render(**ctx), encoding="utf-8")
    return out_file
