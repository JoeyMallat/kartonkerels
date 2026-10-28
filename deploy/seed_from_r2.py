"""First-start seeding: download seed/kartonkerels.db from R2 when no replica exists yet.

Uses only the standard library (AWS Signature V4, which R2 supports).
Exit 0 when seeded or when no seed exists; exit 1 on any other error.
"""
import datetime
import hashlib
import hmac
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request


def sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signed_headers(method, host, path, access_key, secret_key, now, region="auto", payload="UNSIGNED-PAYLOAD"):
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    canonical = "\n".join([
        method, path, "",
        f"host:{host}", f"x-amz-content-sha256:{payload}", f"x-amz-date:{amz_date}", "",
        "host;x-amz-content-sha256;x-amz-date", payload,
    ])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = sign(sign(sign(sign(("AWS4" + secret_key).encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={signature}",
    }


def main():
    env = os.environ
    target = env["DATABASE_PATH"]
    host = f"{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    key = env.get("R2_SEED_KEY", "seed/kartonkerels.db")
    path = "/" + urllib.parse.quote(env["R2_BUCKET"]) + "/" + urllib.parse.quote(key)
    headers = signed_headers("GET", host, path, env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"], datetime.datetime.now(datetime.timezone.utc))
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://{host}{path}", headers=headers), timeout=120) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"No seed database at {env['R2_BUCKET']}/{key}; starting with an empty database.")
            return 0
        print(f"Seed download failed: HTTP {e.code} {e.read()[:300]!r}", file=sys.stderr)
        return 1
    tmp = target + ".seed"
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        c = sqlite3.connect(tmp)
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            c.close()
    except sqlite3.DatabaseError:
        ok = False
    if not ok:
        os.remove(tmp)
        print("Seed database is not a valid SQLite database; refusing to use it.", file=sys.stderr)
        return 1
    os.replace(tmp, target)
    print(f"Seeded database from {env['R2_BUCKET']}/{key} ({len(data)} bytes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
