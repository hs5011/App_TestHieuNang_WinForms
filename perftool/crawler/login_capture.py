"""Tạo mẫu body đăng nhập ({{USERNAME}} / {{PASSWORD}}) từ request đăng nhập bắt được ở bước 3.

Nguyên tắc: thay theo TÊN TRƯỜNG (username/password/matkhau…) và theo giá trị KHỚP NGUYÊN VẸN, không dùng str.replace
trên cả body (admin/admin123 hay mật khẩu "123" trùng số khác trong body sẽ làm hỏng mẫu). Mẫu chỉ được trả về khi
chắc chắn mật khẩu thật đã được gỡ khỏi body; OTP/CAPTCHA (dùng 1 lần) bị để trống, không lưu.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote_plus

USER_PH, PASS_PH = "{{USERNAME}}", "{{PASSWORD}}"

_USER_KEY = re.compile(r"^(user(_?name)?|user_?id|login(_?name|_?id)?|account(_?name)?|email|e_?mail|uid|"
                       r"tai_?khoan|ten_?dang_?nhap|ma_?can_?bo|username_?or_?email)$", re.I)
_PASS_KEY = re.compile(r"^(pass|password|passwd|pwd|mat_?khau|user_?password|login_?password)$", re.I)
_OTP_KEY = re.compile(r"(otp|totp|2fa|mfa|captcha|verif(y|ication)_?code|ma_?xac_?(nhan|thuc)|^code$|^pin$)", re.I)


def json_inner(s: str) -> str:
    """Chuỗi như khi nằm trong 1 chuỗi JSON (mật khẩu có " \\ hoặc chữ Việt dạng \\uXXXX)."""
    return json.dumps(s or "")[1:-1]


def _url_quote(s: str) -> str:
    return quote_plus(s or "")


def body_has_secret(body: str, pwd: str) -> bool:
    """Body có chứa mật khẩu (dạng thô / URL-encode / JSON-escape)."""
    return bool(pwd) and any(v and v in (body or "") for v in (pwd, _url_quote(pwd), json_inner(pwd)))


def parse_body(body: str, ctype: str) -> Any:
    """JSON -> dict/list; form x-www-form-urlencoded -> list[(khoá, giá trị)]; không nhận ra -> None."""
    b = (body or "").strip()
    if b[:1] in ("{", "["):
        try:
            return json.loads(b)
        except ValueError:
            return None
    if "x-www-form-urlencoded" in (ctype or "").lower() or re.fullmatch(r"[^=&\s]+=[^&]*(?:&[^=&\s]+=[^&]*)*", b):
        try:
            return parse_qsl(b, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return None
    return None


def has_secret_field(body: str, ctype: str) -> bool:
    """Body có trường mật khẩu / OTP (dùng khi người dùng tự đăng nhập, không nhập mật khẩu ở bước 1)."""
    data = parse_body(body, ctype)
    if isinstance(data, list) and data and isinstance(data[0], tuple):
        keys = [k for k, _ in data]
    else:
        keys = list(_walk_keys(data))
    return any(_PASS_KEY.match(str(k)) or _OTP_KEY.search(str(k)) for k in keys)


def _walk_keys(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _sub(key: Any, val: Any, user: str, pwd: str) -> tuple[Any, str]:
    """(giá trị mới, loại: 'pass' | 'user' | 'otp' | '')."""
    if isinstance(val, bool) or not isinstance(val, (str, int, float)):
        return val, ""
    k, v = str(key), str(val)
    # tên trường xét trước giá trị: tài khoản kiểu user01/user01 vẫn tách đúng 2 trường
    if _PASS_KEY.match(k):
        return PASS_PH, "pass"
    if _USER_KEY.match(k):
        return USER_PH, "user"
    if pwd and v == pwd:
        return PASS_PH, "pass"
    if user and v == user:
        return USER_PH, "user"
    if _OTP_KEY.search(k) and v:
        return "", "otp"
    return val, ""


# trường bí mật cần che khi lưu request bất kỳ (form đổi mật khẩu, đăng nhập lại giữa lúc ghi, OTP, khoá API trong body)
_SECRET_KEY = re.compile(r"^(pass|password|passwd|pwd|mat_?khau|user_?password|login_?password|old_?password|"
                         r"new_?password|confirm_?password|re_?password|current_?password|mat_?khau_?(cu|moi))$|"
                         r"(otp|totp|2fa|mfa|captcha|client_?secret|api_?secret|access_?token|refresh_?token)", re.I)
REDACTED = "***"


def redact_body(body: Optional[str], secrets: tuple[str, ...] | list[str] = ()) -> tuple[Optional[str], bool]:
    """Che giá trị nhạy cảm trong body request trước khi lưu xuống đĩa: trường mật khẩu/OTP/secret (theo tên khoá) và
    mọi giá trị trùng mật khẩu đã biết. Trả về (body đã che, có che hay không)."""
    if not body:
        return body, False
    secrets = [s for s in secrets if s and len(s) >= 3]
    hit = False

    def bad(k: Any, v: Any) -> bool:
        return isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v) != "" and (
            bool(_SECRET_KEY.search(str(k))) or str(v) in secrets)

    data = parse_body(body, "")
    if isinstance(data, list) and data and isinstance(data[0], tuple):
        parts = []
        for k, v in data:
            if bad(k, v):
                v, hit = REDACTED, True
            parts.append(f"{quote_plus(k)}={quote_plus(str(v)) if v != REDACTED else v}")
        out = "&".join(parts)
    elif isinstance(data, (dict, list)):
        def walk(o: Any) -> Any:
            nonlocal hit
            if isinstance(o, dict):
                res = {}
                for k, v in o.items():
                    if isinstance(v, (dict, list)):
                        res[k] = walk(v)
                    elif bad(k, v):
                        res[k], hit = REDACTED, True
                    else:
                        res[k] = v
                return res
            return [walk(x) for x in o] if isinstance(o, list) else o
        out = json.dumps(walk(data), ensure_ascii=False, separators=(",", ":"))
    else:
        out = body
    for s in secrets:                       # body dạng khác / mật khẩu nằm trong chuỗi lớn hơn
        for enc in dict.fromkeys((s, _url_quote(s), json_inner(s))):
            if enc and enc in out:
                out, hit = out.replace(enc, REDACTED), True
    return (out if hit else body), hit


def login_template(body: str, ctype: str, user: str, pwd: str) -> tuple[Optional[str], list[str]]:
    """Trả về (mẫu body, ghi chú). Mẫu = None khi không chắc đã gỡ được mật khẩu khỏi body (không được lưu)."""
    data = parse_body(body, ctype)
    kinds: list[str] = []
    if isinstance(data, list) and data and isinstance(data[0], tuple):      # form urlencoded: giữ thứ tự trường
        parts = []
        for k, v in data:
            nv, kind = _sub(k, v, user, pwd)
            kinds.append(kind)
            parts.append(f"{quote_plus(k)}={nv if nv in (USER_PH, PASS_PH) else quote_plus(str(nv))}")
        tpl = "&".join(parts)
    elif isinstance(data, (dict, list)):
        def walk(o: Any) -> Any:
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    if isinstance(v, (dict, list)):
                        out[k] = walk(v)
                    else:
                        out[k], kind = _sub(k, v, user, pwd)
                        kinds.append(kind)
                return out
            return [walk(x) for x in o] if isinstance(o, list) else o
        tpl = json.dumps(walk(data), ensure_ascii=False, separators=(",", ":"))
    elif pwd:
        # body dạng khác (text/xml…): thay nguyên văn, chỉ khi mật khẩu xuất hiện đúng 1 chỗ
        found = [x for x in dict.fromkeys((pwd, _url_quote(pwd), json_inner(pwd))) if x and x in body]
        if len(found) < 1 or sum(body.count(x) for x in found) != 1:
            return None, ["không xác định được vị trí mật khẩu trong body"]
        tpl = body.replace(found[0], PASS_PH)
        if user and user != pwd and tpl.count(user) == 1:
            tpl = tpl.replace(user, USER_PH)
        return tpl, []
    else:
        return None, ["body không phải JSON/form và chưa có mật khẩu để đối chiếu"]
    notes = []
    if "pass" not in kinds:
        return None, ["không tìm thấy trường mật khẩu trong body"]
    if "otp" in kinds:
        notes.append("body có mã OTP/CAPTCHA (dùng 1 lần) – đã để trống; đăng nhập tự động trong script có thể "
                     "không thành công, khi đó chọn chế độ dùng cookie/token phiên đã ghi nhận ở bước 7")
    return tpl, notes


# header gắn với PHIÊN đăng nhập (giá trị cũ vô dụng khi chạy lại, lộ ra thì chiếm được phiên): bỏ khi lưu.
# Khoá API tĩnh (x-api-key) được giữ vì script cần gửi lại.
_SESSION_HEADER = re.compile(r"(csrf|xsrf|requestverificationtoken|antiforgery|session|cookie|^authorization$|"
                             r"auth[-_]?token|access[-_]?token|refresh[-_]?token|id[-_]?token)", re.I)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in (headers or {}).items() if not _SESSION_HEADER.search(k)}
