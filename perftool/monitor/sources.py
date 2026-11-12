"""Kết nối các nguồn số liệu: Performance Monitor (SSH / máy cục bộ), CSDL, Zabbix, Prometheus.

Mọi thao tác đều CHỈ ĐỌC: đọc bộ đếm hiệu năng, chạy câu SELECT đếm kết nối, gọi API lấy lịch sử.
"""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime
from typing import Callable, Iterator, Optional

import requests

from ..models import DBMonitor, MonitorConfig, MonitorServer
from . import perfmon

Row = tuple[datetime, str, float]          # (thời điểm, metric, giá trị)

# ---------------------------------------------------------------- khoá bí mật (Windows Credential Manager)


def ssh_secret_key(s: MonitorServer) -> str:
    return f"ssh:{s.host}:{s.ssh_user}"


def db_secret_key(db: DBMonitor) -> str:
    return f"db:{db.db_type}:{db.host}:{db.username}"


ZABBIX_SECRET, PROM_SECRET = "zabbix:secret", "prometheus:token"


# ---------------------------------------------------------------- Performance Monitor

def known_hosts_file():
    from ..config import WORKSPACE_DIR
    return WORKSPACE_DIR / "known_hosts"


def _ssh_client(s: MonitorServer, password: str, trust_new: bool = False):
    """Kết nối SSH có kiểm tra khoá máy chủ (chống giả mạo máy chủ để lấy mật khẩu SSH).

    Khoá tin cậy: ~/.ssh/known_hosts + workspace/known_hosts. Máy chưa biết chỉ được ghi nhận khi người dùng CHỦ ĐỘNG bấm
    "Kiểm tra kết nối" (trust_new=True) – hoặc security.ssh_auto_add_host=true; còn lại bị từ chối. Khoá đã ghi nhận mà
    máy chủ trả khoá khác -> luôn từ chối (BadHostKeyException)."""
    import paramiko
    from .. import config
    kh = known_hosts_file()
    c = paramiko.SSHClient()
    try:
        c.load_system_host_keys()      # ~/.ssh/known_hosts
    except OSError:
        pass
    try:
        if kh.exists():
            c.load_host_keys(str(kh))      # khoá mới cũng được lưu vào file này
        else:
            kh.parent.mkdir(parents=True, exist_ok=True)
            kh.touch()
            c.load_host_keys(str(kh))
    except OSError:
        pass
    if trust_new or config.get("security.ssh_auto_add_host", False):
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())    # AutoAddPolicy lưu vào file load_host_keys
    else:
        c.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        c.connect(s.host, port=int(s.ssh_port or 22), username=s.ssh_user, password=password or None,
                  key_filename=s.ssh_key_file or None, timeout=15, banner_timeout=15, auth_timeout=15,
                  allow_agent=False, look_for_keys=False)
    except paramiko.BadHostKeyException as e:
        raise RuntimeError(f"Khoá máy chủ SSH {s.host} KHÁC khoá đã ghi nhận – có thể bị giả mạo máy chủ. Nếu máy chủ vừa "
                           f"cài lại, xoá dòng của máy này trong {kh} rồi kiểm tra kết nối lại.") from e
    except paramiko.SSHException as e:
        if "not found in known_hosts" in str(e):
            raise RuntimeError(f"Máy chủ SSH {s.host} chưa được tin cậy – bấm 'Kiểm tra kết nối' ở bước 6 để xác nhận "
                               "khoá máy chủ trước khi chạy test.") from e
        raise
    return c


def host_fingerprint(s: MonitorServer) -> str:
    """Vân tay SHA256 của khoá máy chủ đã ghi nhận (hiển thị cho người dùng đối chiếu)."""
    import base64
    import hashlib

    import paramiko
    try:
        hk = paramiko.HostKeys(str(known_hosts_file()))
    except (OSError, paramiko.SSHException):
        return ""
    port = int(s.ssh_port or 22)
    entry = hk.lookup(s.host if port == 22 else f"[{s.host}]:{port}") or {}
    for typ, key in entry.items():
        return f"{typ} SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    return ""


def _ssh_run(c, cmd: str, timeout: int = 60) -> str:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    data = out.read() + err.read()
    return data.decode("utf-8", errors="replace")


def total_ram_mb(s: MonitorServer, password: str = "") -> Optional[float]:
    if s.access == "local":
        txt = subprocess.run(perfmon.TOTAL_RAM_CMD, shell=True, capture_output=True, text=True, timeout=60).stdout
    else:
        c = _ssh_client(s, password)
        try:
            txt = _ssh_run(c, perfmon.TOTAL_RAM_CMD)
        finally:
            c.close()
    m = re.search(r"\d{6,}", txt or "")
    return int(m.group()) / 1024 / 1024 if m else None


def _sleep(seconds: float, stop: Callable[[], bool]) -> bool:
    """Chờ tối đa `seconds`, dừng sớm khi stop() = True. Trả về True nếu bị yêu cầu dừng."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop():
            return True
        time.sleep(min(0.5, max(end - time.monotonic(), 0)))
    return stop()


def _local_stream(interval_s: int, stop: Callable[[], bool], samples: Optional[int]) -> Iterator[Row]:
    """Số liệu máy chạy test đọc bằng psutil (cùng bộ metric với Performance Monitor).

    Không dùng typeperf chạy liên tục: khi stdout là ống dẫn, typeperf dồn bộ đệm ~4 KB nên lượt test ngắn không
    nhận được mẫu nào, và tiến trình con dễ bị bỏ lại khi dừng.
    """
    import psutil
    psutil.cpu_percent(None)
    disk0, net0, t0 = psutil.disk_io_counters(), psutil.net_io_counters(), time.monotonic()
    n = 0
    while not _sleep(interval_s, stop):
        disk1, net1, t1 = psutil.disk_io_counters(), psutil.net_io_counters(), time.monotonic()
        dt = max(t1 - t0, 1e-6)
        ts = datetime.now().replace(microsecond=0)
        rows: list[Row] = [(ts, "cpu", psutil.cpu_percent(None)), (ts, "ram", psutil.virtual_memory().percent)]
        if disk0 and disk1:
            busy_ms = (disk1.read_time - disk0.read_time) + (disk1.write_time - disk0.write_time)
            rows += [(ts, "disk_busy", min(100.0, max(0.0, busy_ms / (dt * 1000) * 100))),
                     (ts, "disk_read", (disk1.read_bytes - disk0.read_bytes) / dt / 1024 / 1024),
                     (ts, "disk_write", (disk1.write_bytes - disk0.write_bytes) / dt / 1024 / 1024)]
        if net0 and net1:
            rows.append((ts, "net", ((net1.bytes_sent + net1.bytes_recv) - (net0.bytes_sent + net0.bytes_recv))
                         / dt / 1024 / 1024))
        disk0, net0, t0 = disk1, net1, t1
        yield from rows
        n += 1
        if samples and n >= samples:
            return


def perfmon_stream(s: MonitorServer, password: str, interval_s: int, stop: Callable[[], bool],
                   samples: Optional[int] = None) -> Iterator[Row]:
    """Đọc bộ đếm hiệu năng định kỳ (qua SSH hoặc trên máy này), trả về từng mẫu với giờ của máy chạy test.

    SSH: mỗi chu kỳ chạy 1 lệnh typeperf ngắn (2 mẫu cách 1 giây, lấy mẫu sau vì mẫu đầu của bộ đếm tốc độ trống).
    Lệnh kết thúc ngay nên dữ liệu không bị kẹt bộ đệm và không để lại tiến trình chạy mãi trên máy chủ.
    """
    if s.access == "local":
        yield from _local_stream(interval_s, stop, samples)
        return
    c = _ssh_client(s, password)
    cmd = perfmon.typeperf_cmd(1, 2)
    n = 0
    try:
        while True:
            t0 = time.monotonic()
            out = _ssh_run(c, cmd)
            if "No valid counters" in out or "Error:" in out:
                raise RuntimeError(f"typeperf báo lỗi: {out.strip()[:300]}")
            parser = perfmon.PdhParser(s.ram_total_mb)
            last: list[Row] = []
            ts = datetime.now().replace(microsecond=0)
            for line in out.splitlines():
                last = parser.feed(line, ts_override=ts) or last
            if not parser.cols:
                raise RuntimeError("Không đọc được bộ đếm hiệu năng (typeperf không trả dữ liệu).")
            yield from last
            n += 1
            if (samples and n >= samples) or _sleep(max(interval_s - (time.monotonic() - t0), 0), stop):
                return
    finally:
        c.close()


def test_server(s: MonitorServer, password: str) -> tuple[dict[str, float], Optional[float]]:
    """Đọc thử 1 mẫu bộ đếm + tổng RAM. Trả về ({metric: giá trị}, tổng RAM MB).

    Người dùng chủ động bấm kiểm tra -> máy SSH chưa biết được ghi nhận khoá vào workspace/known_hosts (tin cậy lần đầu)."""
    if s.access == "ssh":
        _ssh_client(s, password, trust_new=True).close()
    ram = total_ram_mb(s, password)
    s2 = s.model_copy(update={"ram_total_mb": ram or s.ram_total_mb})
    vals: dict[str, float] = {}
    for _, metric, v in perfmon_stream(s2, password, 1, lambda: False, samples=1):
        vals[metric] = v
    return vals, ram


# ---------------------------------------------------------------- CSDL

DEFAULT_PORTS = {"sqlserver": 1433, "postgresql": 5432, "mysql": 3306, "oracle": 1521}
DEFAULT_QUERIES = {
    "sqlserver": "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1",
    "postgresql": "SELECT COUNT(*) FROM pg_stat_activity",
    "mysql": "SELECT COUNT(*) FROM information_schema.PROCESSLIST",
    "oracle": "SELECT COUNT(*) FROM v$session WHERE type = 'USER'",
}


def db_query(db: DBMonitor) -> str:
    return (db.query or "").strip().rstrip(";") or DEFAULT_QUERIES[db.db_type]


def check_readonly(sql: str) -> None:
    """Chỉ cho phép câu truy vấn đọc (SELECT / WITH / SHOW), 1 câu duy nhất.

    Kiểm tra thận trọng (thà chặn nhầm còn hơn bỏ sót): mọi dấu ';' giữa câu đều bị từ chối, kể cả nằm trong chuỗi
    (tránh lách kiểu SELECT '--'; DROP TABLE t), và từ khoá ghi được dò trên TOÀN BỘ câu gốc, kể cả trong chuỗi/chú thích.
    """
    raw = (sql or "").strip().rstrip(";").strip()
    if ";" in raw:
        raise ValueError("Chỉ cho phép 1 câu truy vấn – không dùng dấu ';' ở giữa câu.")
    head = re.sub(r"^(\s*(--[^\n]*\n|/\*.*?\*/))*\s*", "", raw, flags=re.S)     # bỏ chú thích ở đầu câu
    if not re.match(r"(?is)^(select|with|show)\b", head):
        raise ValueError("Chỉ cho phép 1 câu truy vấn đọc dữ liệu (SELECT / WITH / SHOW).")
    if re.search(r"(?i)\b(insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|exec|execute|into|"
                 r"outfile|dumpfile|copy|call|load|lock|set_config|pg_terminate_backend|pg_cancel_backend|"
                 r"pg_reload_conf|pg_read_file|pg_read_binary_file|pg_write_file|pg_ls_dir|pg_stat_file|pg_sleep\w*|"
                 r"setval|nextval|dblink\w*|openrowset|opendatasource|openquery|lo_import|lo_export|dbms_\w+|"
                 r"utl_\w+|xp_\w+|sp_\w+|waitfor|benchmark|sleep)\b", raw):
        raise ValueError("Câu truy vấn chứa lệnh/hàm có thể thay đổi dữ liệu (INSERT, SELECT … INTO, DROP, "
                         "pg_terminate_backend…) – không được phép.")


def db_connect(db: DBMonitor, password: str):
    port = int(db.port or DEFAULT_PORTS[db.db_type])
    if db.db_type == "sqlserver":
        import pymssql
        return pymssql.connect(server=db.host, port=str(port), user=db.username, password=password,
                               database=db.database or "master", login_timeout=15, timeout=15)
    if db.db_type == "postgresql":
        import psycopg
        conn = psycopg.connect(host=db.host, port=port, dbname=db.database or "postgres", user=db.username,
                               password=password, connect_timeout=15,
                               options="-c default_transaction_read_only=on")      # phiên chỉ đọc
        conn.autocommit = True
        return conn
    if db.db_type == "mysql":
        import pymysql
        conn = pymysql.connect(host=db.host, port=port, user=db.username, password=password,
                               database=db.database or None, connect_timeout=15, autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")       # phiên chỉ đọc (MySQL ≥ 5.6.5 / MariaDB)
        except Exception:  # noqa: BLE001 – phiên bản cũ không hỗ trợ: vẫn còn lớp kiểm tra câu truy vấn
            pass
        return conn
    if db.db_type == "oracle":
        import oracledb
        return oracledb.connect(user=db.username, password=password, dsn=f"{db.host}:{port}/{db.database}")
    raise ValueError(f"Loại CSDL không hỗ trợ: {db.db_type}")


def db_sample(conn, sql: str) -> float:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        raise ValueError("Câu truy vấn không trả về dòng nào.")
    for v in row:           # SHOW STATUS trả về (tên, giá trị) -> lấy cột số đầu tiên
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"Kết quả truy vấn không phải số: {row}")


def test_db(db: DBMonitor, password: str) -> float:
    sql = db_query(db)
    check_readonly(sql)
    conn = db_connect(db, password)
    try:
        return db_sample(conn, sql)
    finally:
        conn.close()


# ---------------------------------------------------------------- Zabbix

ZABBIX_ITEMS = {    # mặc định theo template "Windows by Zabbix agent"; giá trị byte/s được đổi sang MB/s
    "cpu": "system.cpu.util",
    "ram": "vm.memory.util",
    "disk_busy": r'perf_counter_en["\PhysicalDisk(_Total)\% Disk Time",60]',
    "disk_read": r'perf_counter_en["\PhysicalDisk(_Total)\Disk Read Bytes/sec",60]',
    "disk_write": r'perf_counter_en["\PhysicalDisk(_Total)\Disk Write Bytes/sec",60]',
}
BYTE_METRICS = {"disk_read", "disk_write", "net"}


class Zabbix:
    def __init__(self, url: str, user: str, secret: str):
        base = url.rstrip("/")
        self.url = base if base.endswith("api_jsonrpc.php") else base + "/api_jsonrpc.php"
        self.token = secret
        self.body_auth = False
        if user:
            try:
                self.token = self._call("user.login", {"username": user, "password": secret}, auth=False)
            except RuntimeError:      # Zabbix < 5.4 dùng tham số "user"
                self.token = self._call("user.login", {"user": user, "password": secret}, auth=False)

    def _call(self, method: str, params: dict, auth: bool = True):
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self.body_auth:
            body["auth"] = self.token
        elif auth:
            headers["Authorization"] = f"Bearer {self.token}"
        r = requests.post(self.url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            err = data["error"]
            if auth and not self.body_auth and "auth" in str(err).lower():
                self.body_auth = True           # Zabbix < 6.4 chưa hỗ trợ header Bearer
                return self._call(method, params, auth)
            raise RuntimeError(f"Zabbix: {err.get('message')} {err.get('data', '')}".strip())
        return data["result"]

    def version(self) -> str:
        return str(self._call("apiinfo.version", {}, auth=False))

    def host_id(self, name: str) -> str:
        for field in ("host", "name"):
            res = self._call("host.get", {"output": ["hostid"], "filter": {field: [name]}})
            if res:
                return res[0]["hostid"]
        raise RuntimeError(f"Không tìm thấy host '{name}' trong Zabbix.")

    def history(self, host: str, items: dict[str, str], t0: datetime, t1: datetime) -> list[Row]:
        hid = self.host_id(host)
        keys = {v: k for k, v in items.items() if v}
        found = self._call("item.get", {"output": ["itemid", "key_", "value_type"], "hostids": hid,
                                        "filter": {"key_": list(keys)}})
        out: list[Row] = []
        for it in found:
            metric = keys.get(it["key_"])
            if not metric or it["value_type"] not in ("0", "3"):
                continue
            hist = self._call("history.get", {"output": "extend", "history": int(it["value_type"]),
                                              "itemids": it["itemid"], "time_from": int(t0.timestamp()),
                                              "time_till": int(t1.timestamp()), "sortfield": "clock"})
            for h in hist:
                v = float(h["value"])
                out.append((datetime.fromtimestamp(int(h["clock"])), metric,
                            v / 1024 / 1024 if metric in BYTE_METRICS else v))
        return out


def zabbix_items(cfg: MonitorConfig) -> dict[str, str]:
    return {**ZABBIX_ITEMS, **{k: v for k, v in cfg.zabbix_items.items() if v is not None}}


# ---------------------------------------------------------------- Prometheus / Grafana

PROM_PRESETS = {
    "windows_exporter": {
        "cpu": '100 - avg(rate(windows_cpu_time_total{mode="idle",instance="$instance"}[1m])) * 100',
        "ram": '100 - windows_os_physical_memory_free_bytes{instance="$instance"} '
               '/ windows_cs_physical_memory_bytes{instance="$instance"} * 100',
        "disk_busy": '100 - avg(rate(windows_logical_disk_idle_seconds_total{instance="$instance"}[1m])) * 100',
        "disk_read": 'sum(rate(windows_logical_disk_read_bytes_total{instance="$instance"}[1m])) / 1048576',
        "disk_write": 'sum(rate(windows_logical_disk_write_bytes_total{instance="$instance"}[1m])) / 1048576',
        "net": 'sum(rate(windows_net_bytes_total{instance="$instance"}[1m])) / 1048576',
    },
    "node_exporter": {
        "cpu": '100 - avg(rate(node_cpu_seconds_total{mode="idle",instance="$instance"}[1m])) * 100',
        "ram": '100 - node_memory_MemAvailable_bytes{instance="$instance"} '
               '/ node_memory_MemTotal_bytes{instance="$instance"} * 100',
        "disk_busy": 'max(rate(node_disk_io_time_seconds_total{instance="$instance"}[1m])) * 100',
        "disk_read": 'sum(rate(node_disk_read_bytes_total{instance="$instance"}[1m])) / 1048576',
        "disk_write": 'sum(rate(node_disk_written_bytes_total{instance="$instance"}[1m])) / 1048576',
        "net": 'sum(rate(node_network_receive_bytes_total{instance="$instance",device!="lo"}[1m]) '
               '+ rate(node_network_transmit_bytes_total{instance="$instance",device!="lo"}[1m])) / 1048576',
    },
}


def prom_queries(cfg: MonitorConfig) -> dict[str, str]:
    return {**PROM_PRESETS["windows_exporter"], **{k: v for k, v in cfg.prom_queries.items() if v is not None}}


def _prom_get(url: str, token: str, path: str, params: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url.rstrip("/") + path, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus: {data.get('error', data)}")
    return data["data"]


def prom_test(url: str, token: str) -> str:
    d = _prom_get(url, token, "/api/v1/query", {"query": "up"})
    return f"{len(d.get('result', []))} target đang được giám sát"


def prom_history(url: str, token: str, queries: dict[str, str], instance: str, t0: datetime, t1: datetime,
                 step_s: int = 15) -> list[Row]:
    out: list[Row] = []
    for metric, q in queries.items():
        if not q:
            continue
        d = _prom_get(url, token, "/api/v1/query_range", {"query": q.replace("$instance", instance),
                                                          "start": t0.timestamp(), "end": t1.timestamp(),
                                                          "step": max(int(step_s), 1)})
        for series in d.get("result", []):
            for ts, v in series.get("values", []):
                try:
                    out.append((datetime.fromtimestamp(float(ts)), metric, float(v)))
                except ValueError:
                    continue
    return out
