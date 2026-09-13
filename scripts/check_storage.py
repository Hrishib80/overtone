"""Say exactly why uploads are or are not working.

    python scripts/check_storage.py

Storage has a lot of ways to be almost configured, and Supabase reports most
of them as a bare `400`. This walks the same path an upload takes and names
the first thing that is actually wrong.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from backend.config import settings  # noqa: E402

OK = "  ok   "
BAD = "  FAIL "
NOTE = "       "


def key_role(key: str) -> str | None:
    """Which key this is, read out of the token itself.

    Supabase keys are JWTs carrying a `role` claim, so "did I paste the anon
    key or the service role key" is a question the key can answer — and it is
    the single most common reason server-side uploads fail, because the anon
    key is subject to row-level security and signing an upload is a write.
    """
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def check_local() -> int:
    root = Path(settings.media_root)
    print(f"{OK} provider: local")
    print(f"{NOTE} writing into {root.resolve()}")
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        print(f"{BAD} that directory is not writable: {exc}")
        return 1
    print(f"{OK} directory is writable — uploads will work")
    print(f"{NOTE} bytes pass through the API in this mode, so it is development only")
    return 0


def check_supabase() -> int:
    print(f"{OK} provider: supabase")
    problems = 0

    if not settings.supabase_url or not settings.supabase_key:
        print(f"{BAD} SUPABASE_URL and SUPABASE_KEY must both be set")
        return 1

    host = settings.supabase_url.replace("https://", "").replace("http://", "").rstrip("/")
    try:
        socket.gethostbyname(host)
        print(f"{OK} {host} resolves")
    except OSError:
        print(f"{BAD} {host} does not resolve")
        print(f"{NOTE} a paused or deleted Supabase project looks exactly like this")
        return 1

    role = key_role(settings.supabase_key)
    if role == "service_role":
        print(f"{OK} SUPABASE_KEY is the service role key")
    elif role == "anon":
        print(f"{BAD} SUPABASE_KEY is the *anon* key")
        print(f"{NOTE} signing an upload is a server-side write, and the anon key is")
        print(f"{NOTE} subject to row-level security, which refuses it as a bare 400.")
        print(f"{NOTE} Use the service role key: Supabase dashboard -> Project Settings")
        print(f"{NOTE} -> API -> Project API keys -> service_role. Keep it server-side;")
        print(f"{NOTE} it bypasses RLS and must never reach a browser.")
        problems += 1
    else:
        print(f"{NOTE} could not read a role out of SUPABASE_KEY (role={role!r})")

    headers = {"Authorization": f"Bearer {settings.supabase_key}", "apiKey": settings.supabase_key}
    try:
        response = httpx.get(f"{settings.supabase_url}/storage/v1/bucket", headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        print(f"{BAD} could not reach storage: {exc}")
        return 1

    if response.status_code != 200:
        print(f"{BAD} listing buckets failed: {response.status_code} {response.text[:160]}")
        problems += 1
    else:
        names = [b["name"] for b in response.json()]
        if not names:
            print(f"{BAD} the project has no storage buckets at all")
        elif settings.supabase_bucket in names:
            print(f"{OK} bucket {settings.supabase_bucket!r} exists")
        else:
            print(f"{BAD} no bucket named {settings.supabase_bucket!r}")
            print(f"{NOTE} buckets that do exist: {', '.join(names)}")
        if settings.supabase_bucket not in names:
            print(f"{NOTE} create it: Supabase dashboard -> Storage -> New bucket ->")
            print(f"{NOTE} name it {settings.supabase_bucket!r}, and make it public so")
            print(f"{NOTE} profile photos can be served without a signed read.")
            problems += 1

    # The real thing: ask for a signed upload the way the app does.
    try:
        signed = httpx.post(
            f"{settings.supabase_url}/storage/v1/object/upload/sign/"
            f"{settings.supabase_bucket}/diagnostic/probe.jpg",
            headers=headers,
            json={"expiresIn": 60},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        print(f"{BAD} signing failed: {exc}")
        return 1

    if signed.status_code in (200, 201):
        print(f"{OK} signing an upload works — photos will upload")
    else:
        try:
            detail = signed.json().get("message") or signed.text
        except Exception:
            detail = signed.text
        print(f"{BAD} signing an upload failed: {signed.status_code} {detail}")
        problems += 1

    return 1 if problems else 0


def main() -> int:
    print(f"\nENVIRONMENT={settings.environment}  STORAGE_PROVIDER={settings.storage_provider}\n")
    code = check_local() if settings.storage_provider == "local" else check_supabase()
    print()
    if code:
        print("  Uploads will not work until the FAIL lines above are fixed.")
        print("  To keep working meanwhile, set STORAGE_PROVIDER=local.\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
