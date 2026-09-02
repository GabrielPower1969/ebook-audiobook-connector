"""Static file server with HTTP Range support (required for <audio> seeking)."""
import http.server, mimetypes, os, re, urllib.parse
mimetypes.add_type("audio/mp4", ".m4b"); mimetypes.add_type("audio/mp4", ".m4a")

def serve(root: str, port: int = 8765, host: str = "127.0.0.1"):
    ROOT = os.path.abspath(root)
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urllib.parse.unquote(self.path.split("?")[0]).lstrip("/") or "index.html"
            fp = os.path.abspath(os.path.join(ROOT, path))
            if os.path.isdir(fp): fp = os.path.join(fp, "index.html")
            if not fp.startswith(ROOT) or not os.path.isfile(fp): self.send_error(404); return
            size = os.path.getsize(fp); start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if m:
                if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else: start = size - int(m.group(2))
                end = min(end, size - 1); self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else: self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(fp)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk: break
                    try: self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError): return
                    left -= len(chunk)
        def log_message(self, *a): pass
    print(f"serving {ROOT} at http://{host}:{port}")
    http.server.ThreadingHTTPServer((host, port), H).serve_forever()
