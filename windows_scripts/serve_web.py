import os
import sys
from pathlib import Path
import http.server
import socketserver

boss_dist = Path(__file__).resolve().parent.parent / "source_code" / "0.3_老板端安卓App_天府掌舵" / "dist"
if boss_dist.exists() and (boss_dist / "index.html").exists():
    dist_dir = boss_dist
else:
    dist_dir = Path(__file__).resolve().parent.parent / "source_code" / "0.1_税务管理" / "gtp_V1.0_FULL" / "01_当前完整系统_V1.0" / "chengdu_construction_tax_system_v1_0" / "app" / "static_dist"

class SPAHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(dist_dir), **kwargs)

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) or os.path.isdir(path):
            self.path = "/index.html"
        return super().do_GET()

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        try:
            sys.stdout.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {format % args}\n")
            sys.stdout.flush()
        except Exception:
            pass

class QuietTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            return
        super().handle_error(request, client_address)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    with QuietTCPServer(("0.0.0.0", port), SPAHandler) as httpd:
        print(f"Serving static web on http://127.0.0.1:{port} from {dist_dir}...")
        sys.stdout.flush()
        httpd.serve_forever()
