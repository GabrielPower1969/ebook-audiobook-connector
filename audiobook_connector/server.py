"""Static file server with HTTP Range support (required for <audio> seeking), plus a tiny JSON API:

  GET  /api/me                 → {"user": "<email>|local", "auth": "cloudflare|local"}
  GET  /api/progress           → {slug: {id, f, t, ts}}        all books for this user
  GET  /api/progress/<slug>    → {id, f, t, ts} or {}
  POST /api/progress/<slug>    ← {id, f, t}                    saves reading position
  GET  /api/marks              → {slug: [mark, ...]}           saved passages, all books
  GET  /api/marks/<slug>       → [mark, ...]
  POST /api/marks/<slug>       ← {items: [mark, ...]}          replaces this book's list

A mark is {k, id, text, note, ts}: k is a client-generated key so the same passage saved on two
devices merges instead of doubling. The browser keeps the same list in localStorage, so an
anonymous LAN reader loses nothing — it simply never leaves the device.

Progress is stored per user under <library>/_progress/<sha1(email)>.json. Identity comes from
auth.identify(): Cloudflare Access JWT when present, else the anonymous "local" user."""
from __future__ import annotations
import hashlib, http.server, json, mimetypes, os, re, threading, time, urllib.parse
from . import auth
mimetypes.add_type("audio/mp4", ".m4b"); mimetypes.add_type("audio/mp4", ".m4a")

def serve(root: str, port: int = 8765, host: str = "0.0.0.0", app_dir: str | None = None,
          verifier: auth.AccessVerifier | None = None, require_auth: bool = False,
          proxy_secret: str | None = None):
    """Serve `root` (the library: data.json, audio, covers). The reader's HTML pages come from
    `app_dir` (the package's app/ folder) so the library holds data only and never goes stale."""
    ROOT = os.path.abspath(root)
    APP = os.path.abspath(app_dir) if app_dir else None
    PROG = os.path.join(ROOT, "_progress")
    os.makedirs(PROG, exist_ok=True)
    lock = threading.Lock()

    def prog_file(user: str) -> str:
        return os.path.join(PROG, hashlib.sha1(user.encode()).hexdigest()[:16] + ".json")

    def load_prog(user: str) -> dict:
        try:
            with open(prog_file(user)) as f: return json.load(f)
        except (OSError, ValueError):
            return {"user": user, "books": {}, "marks": {}}

    class H(http.server.BaseHTTPRequestHandler):
        def _json(self, code: int, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _user(self):
            user, source = auth.identify(self.headers, verifier, require_auth, proxy_secret)
            if user is None:
                self._json(401, {"error": "sign in required"})
            return user, source

        def _api(self, path: str, body: dict | None):
            user, source = self._user()
            if user is None: return
            if path == "api/me":
                return self._json(200, {"user": user, "auth": source})
            if path == "api/progress":
                return self._json(200, load_prog(user)["books"])
            if path == "api/marks":
                return self._json(200, load_prog(user).get("marks", {}))
            m = re.fullmatch(r"api/marks/([A-Za-z0-9._-]+)", path)
            if m:
                slug = m.group(1)
                if body is None:
                    return self._json(200, load_prog(user).get("marks", {}).get(slug, []))
                if source == "local":
                    return self._json(200, {"stored": False})
                items = body.get("items")
                if not isinstance(items, list) or len(items) > 2000:
                    return self._json(400, {"error": "expected {items: [...]}, at most 2000"})
                clean = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    clean.append({"k": str(it.get("k", ""))[:64], "id": int(it.get("id", 0)),
                                  "text": str(it.get("text", ""))[:2000], "note": str(it.get("note", ""))[:2000],
                                  "ts": int(it.get("ts", 0))})
                with lock:
                    d = load_prog(user); d.setdefault("marks", {})[slug] = clean
                    tmp = prog_file(user) + ".tmp"
                    with open(tmp, "w") as f: json.dump(d, f)
                    os.replace(tmp, prog_file(user))
                return self._json(200, {"stored": True, "count": len(clean)})
            m = re.fullmatch(r"api/progress/([A-Za-z0-9._-]+)", path)
            if not m: return self._json(404, {"error": "unknown endpoint"})
            slug = m.group(1)
            if body is None:
                return self._json(200, load_prog(user)["books"].get(slug, {}))
            if source == "local":                       # anonymous LAN readers keep progress in the browser
                return self._json(200, {"stored": False})
            try:
                rec = {"id": int(body["id"]), "f": int(body["f"]), "t": float(body["t"]), "ts": int(time.time())}
            except (KeyError, TypeError, ValueError):
                return self._json(400, {"error": "expected {id, f, t}"})
            with lock:
                d = load_prog(user); d["books"][slug] = rec
                tmp = prog_file(user) + ".tmp"
                with open(tmp, "w") as f: json.dump(d, f)
                os.replace(tmp, prog_file(user))
            return self._json(200, {"stored": True, **rec})

        def do_POST(self):
            path = urllib.parse.unquote(self.path.split("?")[0]).lstrip("/")
            if not path.startswith("api/"): self.send_error(405); return
            n = int(self.headers.get("Content-Length") or 0)
            try: body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError: return self._json(400, {"error": "bad json"})
            self._api(path, body if isinstance(body, dict) else {})
        do_PUT = do_POST

        def do_GET(self):
            path = urllib.parse.unquote(self.path.split("?")[0]).lstrip("/") or "index.html"
            if path.startswith("api/"):
                return self._api(path, None)
            if path.startswith("_progress"):            # never serve other users' progress files
                self.send_error(404); return
            if proxy_secret or require_auth or (verifier and (self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"))):
                if auth.identify(self.headers, verifier, require_auth, proxy_secret)[0] is None:
                    self.send_error(401); return
            fp = os.path.abspath(os.path.join(ROOT, path))
            if os.path.isdir(fp): fp = os.path.join(fp, "index.html")
            if not fp.startswith(ROOT): self.send_error(404); return
            if not os.path.isfile(fp) and APP and path.endswith(".html") and "/" not in path:
                fp = os.path.join(APP, path)            # index.html / reader.html live in the package
            if not os.path.isfile(fp): self.send_error(404); return
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            # Pre-compressed sibling (build writes data.json.gz). Only without a Range request:
            # a byte range of the compressed stream is not a byte range of the file.
            enc = None
            rng = self.headers.get("Range")
            if not rng and "gzip" in (self.headers.get("Accept-Encoding") or "") and os.path.isfile(fp + ".gz"):
                fp, enc = fp + ".gz", "gzip"
            size = os.path.getsize(fp); start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m:
                if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else: start = size - int(m.group(2))
                end = min(end, size - 1); self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else: self.send_response(200)
            self.send_header("Content-Type", ctype)
            if enc: self.send_header("Content-Encoding", enc)
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
    srv = http.server.ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    print(f"  serving {ROOT}   (Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
