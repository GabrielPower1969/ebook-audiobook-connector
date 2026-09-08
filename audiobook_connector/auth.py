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
