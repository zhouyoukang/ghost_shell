"""
🎤 Speech Recognition Test Server
运行在 4443 端口，用于独立测试语音识别模块
"""

import http.server
import ssl
import os
import sys

PORT = 4443
CERT_FILE = os.path.join(os.path.dirname(__file__), "..", "ghost_shell", "server.pem")

# 使用 ghost_shell 的证书，或者当前目录的
if not os.path.exists(CERT_FILE):
    CERT_FILE = os.path.join(os.path.dirname(__file__), "server.pem")
    if not os.path.exists(CERT_FILE):
        print("❌ 未找到 SSL 证书 (server.pem)")
        print("   请将证书放在 speech/ 或 ghost_shell/ 目录")
        sys.exit(1)

class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(__file__) or '.', **kwargs)
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

if __name__ == "__main__":
    print(f"🎤 Speech Test Server starting on port {PORT}...")
    print(f"   Using cert: {CERT_FILE}")
    
    server = http.server.HTTPServer(('0.0.0.0', PORT), CORSHandler)
    
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    
    # 获取本机IP
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"\n{'='*50}")
    print(f"✅ Speech Test Server Ready!")
    print(f"   Local:  https://localhost:{PORT}")
    print(f"   Mobile: https://{local_ip}:{PORT}")
    print(f"{'='*50}\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped")
