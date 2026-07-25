# -*- coding: utf-8 -*-
"""
Orbit Hive 交付物静态服务器（只读）
====================================
把整个仓库作为只读静态目录 expose 在 127.0.0.1:8790，
使所有 HTML 交付物（reports/、outputs/、web/ 等）都能拿到
http://127.0.0.1:8790/<相对路径> 的可点击直开链接，塞进飞书卡。

用法（由 start_stage3 / 开机自启 .bat 拉起，隐藏窗口）：
  pythonw.exe serve_deliverables.py
"""
import http.server
import os
import socketserver

PORT = 8790
# 仓库根 = tiktok_e_comm/ （本文件在 tiktok_e_comm/agent_comms/ 下两级）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=REPO_ROOT, **k)

    def end_headers(self):
        # 禁止缓存，保证飞书链接点开总是最新交付物
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass  # 静默


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print("Orbit Hive deliverables server on http://127.0.0.1:%d/ (root=%s)" % (PORT, REPO_ROOT))
        httpd.serve_forever()
