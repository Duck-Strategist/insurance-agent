# ruff: noqa: E501  # 长行都在嵌入的 JavaScript 和字幕文字里
"""Remove the demo user cleanly (threads via API so checkpoints go too), optionally re-create it as admin.

Usage: python reset_user.py <username> <password> [--recreate]
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "http://localhost:8000/api/v1"
USER, PASSWORD = sys.argv[1], sys.argv[2]


def call(
    method: str, path: str, body: dict | None = None, token: str | None = None
) -> tuple[int, object]:
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode() if body else None
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


def psql(sql: str) -> str:
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "insurance-agent-postgres-1",
            "psql",
            "-U",
            "insurance",
            "-d",
            "insurance",
            "-tA",
        ],
        input=sql,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


status, tok = call("POST", "/auth/login", {"username": USER, "password": PASSWORD})
if status == 200:
    token = tok["access_token"]  # type: ignore[index]
    _, threads = call("GET", "/threads", token=token)
    for t in threads or []:  # type: ignore[union-attr]
        s, _ = call("DELETE", f"/threads/{t['id']}", token=token)
        print("delete thread", t["id"], s)
    left = psql(
        "select count(*) from checkpoints c where c.thread_id in "
        f"(select id::text from chat_threads t join users u on u.id = t.user_id where u.username = '{USER}');"
    )
    print("checkpoints left for user threads:", left)
    print("delete user:", psql(f"delete from users where username = '{USER}' returning id;"))
else:
    print("login failed", status, "(user absent?)")

if "--recreate" in sys.argv:
    s, _ = call("POST", "/auth/register", {"username": USER, "password": PASSWORD})
    print("register", s)
    print(
        subprocess.run(
            [
                "docker",
                "exec",
                "insurance-agent-backend-1",
                "python",
                "-m",
                "app.manage",
                "promote-admin",
                USER,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    )
