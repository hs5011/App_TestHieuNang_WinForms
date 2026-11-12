"""Thu thập số liệu tài nguyên máy chủ (CPU, RAM, I/O, kết nối CSDL) trong thời gian chạy kiểm thử.

Mọi nguồn (Performance Monitor qua SSH/máy cục bộ/file, Zabbix, Prometheus, truy vấn CSDL) đều quy về
một định dạng chung, lưu CSV tại workspace/projects/<id>/monitor/*.csv:
    ts (giờ máy chạy test, "YYYY-mm-dd HH:MM:SS"), server, metric, value, source
Số liệu của một lượt chạy = các mẫu có ts nằm trong [started_at, finished_at] của lượt đó.
"""

METRICS = {
    "cpu": ("CPU", "%"),
    "ram": ("RAM", "%"),
    "disk_busy": ("Ổ đĩa bận", "%"),
    "disk_read": ("Đọc đĩa", "MB/s"),
    "disk_write": ("Ghi đĩa", "MB/s"),
    "net": ("Mạng", "MB/s"),
    "db_conn": ("Kết nối CSDL", "kết nối"),
}
