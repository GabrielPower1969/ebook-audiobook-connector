"""Who is reading? Cloudflare Access identity, verified with the standard library only.

Deployment model: the reader sits behind a Cloudflare Tunnel, and Cloudflare Access (Google login)
guards the public hostname. Every request that made it through Access carries a signed JWT in the
`Cf-Access-Jwt-Assertion` header; its `email` claim is the user. We verify the RS256 signature
against the team's published keys and check aud / iss / exp — never trust the header unverified.

Requests without a token (home LAN, no Cloudflare in the path) are the anonymous "local" user,
whose progress stays in the browser. Requests that came *through* Cloudflare but carry no valid
token are refused: that only happens when Access is misconfigured, so fail closed.

Alternative: a trusted reverse proxy (the flowgt.co.nz Pages Function at /read/*) that has already
authenticated the user. It forwards `X-Flowgt-User: <email>` and proves itself with
`X-Flowgt-Proxy: <shared secret>`. When AC_PROXY_SECRET is set, every request must carry the
secret — a request without it (LAN included) is refused, so the tunnel hostname is useless to
anyone who is not the proxy.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, threading, time, urllib.request

# ASN.1 DigestInfo prefix for SHA-256 (RFC 8017 §9.2, PKCS#1 v1.5 padding)
_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AccessVerifier:
    def __init__(self, team: str, aud: str, certs_url: str | None = None):
        self.iss = f"https://{team}.cloudflareaccess.com"
        self.aud = aud
        self.certs_url = certs_url or f"{self.iss}/cdn-cgi/access/certs"
        self._keys: dict[str, tuple[int, int]] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    def _load_keys(self, force: bool = False) -> dict[str, tuple[int, int]]:
        with self._lock:
            if force or not self._keys or time.time() - self._fetched > 3600:
                with urllib.request.urlopen(self.certs_url, timeout=10) as r:
                    jwks = json.load(r)
                self._keys = {k["kid"]: (int.from_bytes(_b64url(k["n"]), "big"),
                                         int.from_bytes(_b64url(k["e"]), "big"))
                              for k in jwks.get("keys", []) if k.get("kty") == "RSA"}
                self._fetched = time.time()
            return self._keys

    def verify(self, token: str) -> str | None:
        """Return the email claim of a valid token, else None."""
        try:
            h, p, s = token.split(".")
            header = json.loads(_b64url(h))
            if header.get("alg") != "RS256":
                return None
            kid = header.get("kid")
            keys = self._load_keys()
            if kid not in keys:                       # key rotation: refresh once
                keys = self._load_keys(force=True)
            n, e = keys[kid]
            sig = int.from_bytes(_b64url(s), "big")
            em = pow(sig, e, n).to_bytes((n.bit_length() + 7) // 8, "big")
            digest = _SHA256_PREFIX + hashlib.sha256(f"{h}.{p}".encode()).digest()
            expected = b"\x00\x01" + b"\xff" * (len(em) - len(digest) - 3) + b"\x00" + digest
            if not hmac.compare_digest(em, expected):
                return None
            claims = json.loads(_b64url(p))
            aud = claims.get("aud")
            aud = aud if isinstance(aud, list) else [aud]
            if self.aud not in aud or claims.get("iss") != self.iss:
                return None
            if float(claims.get("exp", 0)) < time.time():
                return None
            email = claims.get("email")
            return email.strip().lower() if email else None
        except Exception:
            return None


def identify(headers, verifier: AccessVerifier | None, require_auth: bool = False,
             proxy_secret: str | None = None):
    """→ (user, source) where source is 'proxy' | 'cloudflare' | 'local', or (None, 'denied')."""
    if proxy_secret:
        given = headers.get("X-Flowgt-Proxy") or ""
        if not hmac.compare_digest(given, proxy_secret):
            return None, "denied"
        email = (headers.get("X-Flowgt-User") or "").strip().lower()
        return (email, "proxy") if email else (None, "denied")
    token = headers.get("Cf-Access-Jwt-Assertion")
    if verifier and token:
        email = verifier.verify(token)
        if email:
            return email, "cloudflare"
        return None, "denied"
    via_cloudflare = bool(headers.get("Cf-Ray") or headers.get("Cf-Connecting-Ip"))
    if require_auth or (verifier and via_cloudflare):
        return None, "denied"
    return "local", "local"


# ---------------------------------------------------------------- per-device identity (LAN)
"""On a trusted LAN nobody signs in, but each device should still get its own reading position.

The obvious idea — key on the IP — does not survive contact with DHCP: this machine's own address
moved from .25 to .164 between two sessions, and phones renumber constantly. A MAC address is
stable per network but is only visible for devices on the same layer-2 segment, is randomised per
SSID by every modern phone, and is invisible entirely from inside a container. So the key is a
random id the device keeps in a cookie, which is stable, unguessable and works through any network
path; the IP and MAC are recorded once as a *label*, so a human can tell one device from another.
"""
import re as _re, secrets, subprocess

COOKIE = "ac_device"
_ARP = {}


def device_id(headers) -> str | None:
    """The id this device already carries, or None if it has never been here."""
    for part in (headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and _re.fullmatch(r"[A-Za-z0-9_-]{16,64}", v or ""):
            return v
    return None


def new_device_id() -> str:
    return secrets.token_urlsafe(18)


def cookie_header(did: str) -> str:
    # Ten years, so a device keeps its shelf; Lax because the reader is only ever same-site.
    return f"{COOKIE}={did}; Path=/; Max-Age=315360000; SameSite=Lax"


def mac_of(ip: str) -> str:
    """The MAC behind a LAN address, from the host's own ARP table. Empty when it cannot be known:
    a different subnet, a container, or a client that has not been ARPed yet."""
    if not ip or ip in _ARP:
        return _ARP.get(ip, "")
    _ARP[ip] = ""
    if not _re.match(r"(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", ip):
        return ""
    try:
        out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=1.5).stdout
        m = _re.search(r"(([0-9a-f]{1,2}:){5}[0-9a-f]{1,2})", out, _re.I)
        if m:
            _ARP[ip] = ":".join(f"{int(x, 16):02x}" for x in m.group(1).split(":"))
    except Exception:
        pass
    return _ARP[ip]


def describe(ua: str) -> str:
    """A short, recognisable name for a device, from its user agent."""
    ua = ua or ""
    for pat, name in ((r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"),
                      (r"Macintosh", "Mac"), (r"Windows", "Windows"), (r"CrOS", "ChromeOS"),
                      (r"Linux", "Linux"), (r"Kindle|Silk", "Kindle"), (r"Onyx|Boox", "Boox")):
        if _re.search(pat, ua, _re.I):
            browser = next((b for b in ("Edg", "CriOS", "Chrome", "FxiOS", "Firefox", "Safari")
                            if b in ua), "")
            nice = {"Edg": "Edge", "CriOS": "Chrome", "FxiOS": "Firefox"}.get(browser, browser)
            return f"{name} · {nice}" if nice else name
    return "设备"
