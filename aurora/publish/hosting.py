"""Temporary public HTTPS hosting for Instagram (Meta fetches the video from a URL it can reach).

S3-compatible object store (Cloudflare R2 free tier: 10 GB, no egress fees). Stdlib-only SigV4 presigning, no boto3.
The file is PUT with a presigned URL, handed to Meta as an expiring presigned GET URL, then deleted.
Env (.env): HOST_ENDPOINT (https://<account>.r2.cloudflarestorage.com), HOST_BUCKET, HOST_ACCESS_KEY, HOST_SECRET_KEY, HOST_REGION (auto).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse


class HostingNotConfigured(Exception):
    pass


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign(method: str, endpoint: str, bucket: str, key: str, access: str, secret: str, region: str = "auto",
            expires: int = 3600, now: datetime | None = None) -> str:
    """AWS SigV4 query-string presigned URL (path-style: endpoint/bucket/key)."""
    now = now or datetime.now(timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = urlparse(endpoint).netloc
    path = "/" + quote(f"{bucket}/{key}", safe="/-_.~")
    scope = f"{day}/{region}/s3/aws4_request"
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{access}/{scope}", "X-Amz-Date": amz,
         "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    qs = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(q.items()))
    canonical = f"{method}\n{path}\n{qs}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    to_sign = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{endpoint.rstrip('/')}{path}?{qs}&X-Amz-Signature={sig}"


def _cfg() -> dict:
    c = {k: os.environ.get(f"HOST_{k.upper()}", "") for k in ("endpoint", "bucket", "access_key", "secret_key", "region")}
    c["region"] = c["region"] or "auto"
    if not all(c[k] for k in ("endpoint", "bucket", "access_key", "secret_key")):
        raise HostingNotConfigured("set HOST_ENDPOINT, HOST_BUCKET, HOST_ACCESS_KEY, HOST_SECRET_KEY in .env (see docs/AUTOPILOT.md)")
    return c


@contextmanager
def temporary_public_url(path: str | Path, ttl: int = 3600, http=None):
    """Upload the file, yield an expiring public GET URL, always delete the object afterwards."""
    import requests

    http = http or requests
    c = _cfg()
    key = f"aurora/{uuid.uuid4().hex}{Path(path).suffix}"

    def url(method: str, expires: int) -> str:
        return presign(method, c["endpoint"], c["bucket"], key, c["access_key"], c["secret_key"], c["region"], expires)

    with open(path, "rb") as f:
        r = http.put(url("PUT", 900), data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    r.raise_for_status()
    try:
        yield url("GET", ttl)
    finally:
        try:
            http.delete(url("DELETE", 300), timeout=60)
        except Exception:  # noqa: BLE001 - best effort; set a bucket lifecycle rule (1 day) as backstop
            pass
