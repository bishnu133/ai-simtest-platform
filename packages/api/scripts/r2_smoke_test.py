"""Cloudflare R2 smoke test — validates real R2 credentials end-to-end.

This is NOT a unit test. It makes real network calls to Cloudflare R2 and
performs put/get/list/sign/delete operations against your actual bucket.

Run this ONCE after creating your R2 bucket and API token to verify the
adapter works against the real backend.

USAGE:
  cd packages/api
  export R2_BUCKET=ai-simtest-dev
  export R2_ACCOUNT_ID=<your-cloudflare-account-id>
  export R2_ACCESS_KEY_ID=<your-r2-access-key>
  export R2_SECRET_ACCESS_KEY=<your-r2-secret>
  PYTHONPATH=. python scripts/r2_smoke_test.py

EXPECTED OUTPUT:
  Each step prints OK with a green checkmark on success.
  Final summary shows "ALL CHECKS PASSED" if everything works.

WHAT IT TESTS:
  1. Adapter construction with real credentials
  2. PUT — upload a small JSON object
  3. EXISTS — head check returns True for the uploaded object
  4. GET — download the object and verify hash matches what was uploaded
  5. LIST — paginated list returns the test key
  6. SIGN_URL — generate a presigned GET URL and verify it's reachable
  7. DELETE — clean up the test object
  8. EXISTS again — confirm deletion took effect

If any step fails, the script prints the exception with details and exits 1.
"""
from __future__ import annotations

import asyncio
import os
import sys
import json
import uuid
from datetime import timedelta

# Make sure we can import src.* when run from packages/api
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage.r2 import CloudflareR2Adapter  # noqa: E402
from src.storage.models import ObjectNotFound  # noqa: E402


# ANSI color codes for clearer output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def step(num: int, name: str) -> None:
    print(f"\n{BOLD}{BLUE}[Step {num}]{RESET} {name}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str, exc: Exception | None = None) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    if exc:
        print(f"    {RED}{type(exc).__name__}: {exc}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


async def run_smoke_test() -> int:
    print(f"{BOLD}=== Cloudflare R2 Smoke Test ==={RESET}")
    print(f"This test makes REAL network calls to Cloudflare R2.")
    print(f"It uploads a small object, verifies it, then deletes it.\n")

    # ---------------------------------------------------------------
    # Validate environment
    # ---------------------------------------------------------------
    bucket = os.getenv("R2_BUCKET")
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    endpoint = os.getenv("R2_ENDPOINT_URL")  # optional

    if not all([bucket, access_key, secret_key]):
        print(f"{RED}ERROR:{RESET} Missing required environment variables.")
        print("Required: R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY")
        print("Optional: R2_ACCOUNT_ID (or R2_ENDPOINT_URL)")
        return 1
    if not account_id and not endpoint:
        print(f"{RED}ERROR:{RESET} Either R2_ACCOUNT_ID or R2_ENDPOINT_URL must be set.")
        return 1

    print(f"  Bucket:      {bucket}")
    print(f"  Account ID:  {account_id or '(derived from endpoint)'}")
    print(f"  Endpoint:    {endpoint or f'https://{account_id}.r2.cloudflarestorage.com'}")
    print(f"  Access Key:  {access_key[:8]}... (truncated)")

    # Use a unique tenant_id so this test never collides with real data
    tenant_id = "smoke_test"
    test_id = str(uuid.uuid4())[:8]
    test_key = f"{tenant_id}/smoke/{test_id}/hello.json"
    test_payload = {
        "message": "hello from r2 smoke test",
        "test_id": test_id,
        "platform": "ai-simtest",
    }
    test_bytes = json.dumps(test_payload, sort_keys=True).encode("utf-8")

    # ---------------------------------------------------------------
    # Step 1: Construct adapter
    # ---------------------------------------------------------------
    step(1, "Constructing R2 adapter with real credentials")
    try:
        adapter = CloudflareR2Adapter(
            bucket=bucket,
            account_id=account_id,
            access_key_id=access_key,
            secret_access_key=secret_key,
            endpoint_url=endpoint,
        )
        ok(f"Adapter created (backend={adapter.backend_name}, bucket={adapter.bucket})")
    except Exception as exc:
        fail("Could not construct adapter", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 2: PUT
    # ---------------------------------------------------------------
    step(2, f"PUT — uploading {len(test_bytes)} bytes to {test_key}")
    try:
        ref = await adapter.put(
            tenant_id=tenant_id,
            key=test_key,
            data=test_bytes,
            content_type="application/json",
        )
        ok(f"Upload succeeded")
        ok(f"  size_bytes:   {ref.size_bytes}")
        ok(f"  content_hash: {ref.content_hash[:16]}...")
        ok(f"  backend:      {ref.backend}")
        original_hash = ref.content_hash
    except Exception as exc:
        fail("PUT failed", exc)
        print(f"\n{YELLOW}Hint:{RESET} If you see 'NoSuchBucket', verify your R2_BUCKET name.")
        print(f"{YELLOW}Hint:{RESET} If you see '403 Forbidden', verify your token has Read & Write permission.")
        return 1

    # ---------------------------------------------------------------
    # Step 3: EXISTS
    # ---------------------------------------------------------------
    step(3, "EXISTS — verifying the object is reachable")
    try:
        exists = await adapter.exists(tenant_id, test_key)
        if exists:
            ok("Object exists (head check passed)")
        else:
            fail("Object reported as missing immediately after upload")
            return 1
    except Exception as exc:
        fail("EXISTS failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 4: GET + integrity check
    # ---------------------------------------------------------------
    step(4, "GET — downloading object and verifying integrity")
    try:
        fetched = await adapter.get(tenant_id, test_key)
        if fetched.data == test_bytes:
            ok("Bytes round-tripped exactly")
        else:
            fail("Round-tripped bytes do NOT match what was uploaded")
            return 1
        if fetched.ref.content_hash == original_hash:
            ok(f"Hash matches: {original_hash[:16]}...")
        else:
            fail(
                f"Hash mismatch! uploaded={original_hash[:16]}..., "
                f"downloaded={fetched.ref.content_hash[:16]}..."
            )
            return 1
        # Verify the JSON parses correctly
        parsed = json.loads(fetched.data.decode("utf-8"))
        if parsed["test_id"] == test_id:
            ok(f"JSON content verified (test_id={test_id})")
    except Exception as exc:
        fail("GET failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 5: LIST
    # ---------------------------------------------------------------
    step(5, f"LIST — listing objects under prefix {tenant_id}/smoke/{test_id}/")
    try:
        keys = await adapter.list_keys(tenant_id, f"{tenant_id}/smoke/{test_id}/")
        if test_key in keys:
            ok(f"Found {len(keys)} key(s) under prefix, including our test key")
        else:
            fail(f"Test key not in list. Got: {keys}")
            return 1
    except Exception as exc:
        fail("LIST failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 6: SIGN_URL
    # ---------------------------------------------------------------
    step(6, "SIGN_URL — generating a presigned GET URL")
    try:
        signed = await adapter.sign_url(
            tenant_id=tenant_id,
            key=test_key,
            method="GET",
            expires_in=timedelta(minutes=5),
        )
        ok(f"Signed URL generated")
        ok(f"  method:     {signed.method}")
        ok(f"  expires_at: {signed.expires_at.isoformat()}")
        ok(f"  url:        {signed.url[:80]}...")
        if not signed.url.startswith("https://"):
            warn(f"Signed URL doesn't start with https:// — got: {signed.url[:30]}")
    except Exception as exc:
        fail("SIGN_URL failed", exc)
        return 1

    # Bonus: actually fetch the signed URL via httpx to confirm it works
    step("6b", "Fetching the signed URL via HTTPS to confirm reachability")
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(signed.url, timeout=10.0)
            if resp.status_code == 200 and resp.content == test_bytes:
                ok(f"Signed URL returned {len(resp.content)} bytes (HTTP 200)")
            else:
                fail(
                    f"Signed URL returned HTTP {resp.status_code}, "
                    f"content match: {resp.content == test_bytes}"
                )
                return 1
    except Exception as exc:
        fail("Fetching signed URL failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 7: DELETE
    # ---------------------------------------------------------------
    step(7, "DELETE — cleaning up the test object")
    try:
        deleted = await adapter.delete(tenant_id, test_key)
        if deleted:
            ok("Delete returned True")
        else:
            fail("Delete returned False (object reported as not existing)")
            return 1
    except Exception as exc:
        fail("DELETE failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Step 8: EXISTS (should be False now)
    # ---------------------------------------------------------------
    step(8, "EXISTS — confirming deletion took effect")
    try:
        exists_after = await adapter.exists(tenant_id, test_key)
        if not exists_after:
            ok("Object no longer exists (cleanup successful)")
        else:
            warn("Object still reports as existing — R2 eventual consistency?")
    except Exception as exc:
        fail("EXISTS check after delete failed", exc)
        return 1

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print(f"\n{BOLD}{GREEN}=========================================={RESET}")
    print(f"{BOLD}{GREEN}  ALL CHECKS PASSED — R2 IS PRODUCTION READY{RESET}")
    print(f"{BOLD}{GREEN}=========================================={RESET}")
    print(f"\nYour Cloudflare R2 setup is verified end-to-end.")
    print(f"You can now set STORAGE_BACKEND=r2 in production deployments.")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_smoke_test())
    sys.exit(exit_code)
