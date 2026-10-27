"""Small JSON-over-HTTP helpers on the standard library; every error message has the key replaced by [key]."""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


def request(url: str, key: str, body: dict | None = None, method: str | None = None, timeout: float = 120, tries: int = 3,
            extra_headers: dict | None = None) -> dict:
    """POST (with body) or GET; network errors and 5xx retry, 4xx answers come back as they are."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": config.UA, **(extra_headers or {})}
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")
            if exc.code < 500:
                try:
                    return json.loads(text)
                except ValueError:
                    raise RuntimeError(f"{url} {exc.code}: {text.replace(key, '[key]')[:300]}") from None
            last = f"{exc.code}: {text[:200]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"{url}: {str(last).replace(key, '[key]')[:300]}")


def upload(url: str, key: str, path: Path, fields: dict | None = None, timeout: int = 300) -> dict:
    """Multipart upload with curl (the stdlib has no multipart encoder); retried, since upload endpoints drop connections."""
    cmd = ["curl", "-s", "-X", "POST", url, "-H", "Authorization: Bearer " + key, "-F", f"file=@{path}"]
    for k, v in (fields or {}).items():
        cmd += ["-F", f"{k}={v}"]
    last = ""
    for attempt in range(4):
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
        try:
            return json.loads(out)
        except ValueError:
            last = out.replace(key, "[key]")[:200]
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"upload {path.name} failed: {last}")


def download(url: str, out: Path) -> Path:
    tmp = out.with_suffix(out.suffix + ".part")
    ok = subprocess.run(["curl", "-s", "-L", "--retry", "6", "--retry-all-errors", "--retry-delay", "5", "-o", str(tmp), url],
                        timeout=900).returncode == 0
    if not ok or not tmp.exists() or tmp.stat().st_size < 1000:
        raise RuntimeError(f"download failed: {url[:120]}")
    tmp.replace(out)
    return out
