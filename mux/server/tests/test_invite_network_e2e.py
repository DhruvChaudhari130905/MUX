"""Network-level invite flow through FastAPI and the Supabase Auth HTTP contract.

The backend is a real Uvicorn process with isolated local state. Only Supabase Auth is
faked, so this test never sends email or uses production credentials.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt


SERVER_DIR = Path(__file__).resolve().parents[1]
JWT_SECRET = "local-integration-test-secret-at-least-32-bytes"
SERVICE_KEY = "local-supabase-service-role-test-key"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _token(user_id: str, email: str | None = None) -> str:
    payload = {"sub": user_id, "role": "authenticated", "aud": "authenticated", "exp": int(time.time()) + 3600}
    if email:
        payload["email"] = email
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _auth(user_id: str, email: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, email)}"}


def test_private_room_invite_flow_over_http(tmp_path):
    """Owner invites by email, Supabase accepts/rejects, and invitees join with the assigned roles."""
    provider_requests: list[dict] = []
    response_statuses = [200, 429]
    requests_lock = threading.Lock()

    class SupabaseAuthStub(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size))
            with requests_lock:
                provider_requests.append({
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": body,
                })
                status = response_statuses.pop(0)
            response = {"id": "stub-user"} if status == 200 else {"msg": "email rate limit exceeded"}
            data = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    supabase = ThreadingHTTPServer(("127.0.0.1", 0), SupabaseAuthStub)
    supabase_thread = threading.Thread(target=supabase.serve_forever, daemon=True)
    supabase_thread.start()

    api_port = _free_port()
    app_url = "https://mux-e2e.example.test"
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SERVER_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    env.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'mux.db'}",
        "SUPABASE_URL": f"http://127.0.0.1:{supabase.server_port}",
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "SUPABASE_JWT_SECRET": JWT_SECRET,
        "SUPABASE_JWT_AUDIENCE": "",
        "SUPABASE_JWT_ISSUER": "",
        "WEB_APP_URL": app_url,
        "TOKEN_FACTORY_API_KEY": "",
        "SANDBOX_PROVIDER": "none",
        "ROOM_SECRETS_KEY": "",
        "ALLOW_UNVERIFIED_TOKENS": "false",
    })
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mux.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{api_port}"

    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                output = backend.stdout.read() if backend.stdout else ""
                raise AssertionError(f"Backend exited before becoming healthy:\n{output}")
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("Backend did not become healthy within 20 seconds")

        with httpx.Client(base_url=base_url, timeout=5) as owner:
            created = owner.post("/rooms", json={"description": "Invite E2E room"}, headers=_auth("owner"))
            assert created.status_code == 201, created.text
            room_id = created.json()["id"]

            delivered = owner.post(
                f"/rooms/{room_id}/invites",
                json={"email": " Bob@Example.test ", "role": "viewer"},
                headers=_auth("owner"),
            )
            assert delivered.status_code == 200, delivered.text
            assert delivered.json()["email_sent"] is True
            assert delivered.json()["email"] == "bob@example.test"
            assert delivered.json()["link"] == f"{app_url}/room/{room_id}"

            provider_rejected = owner.post(
                f"/rooms/{room_id}/invites",
                json={"email": "carol@example.test", "role": "editor"},
                headers=_auth("owner"),
            )
            assert provider_rejected.status_code == 200, provider_rejected.text
            assert provider_rejected.json()["email_sent"] is False
            assert "email limit" in provider_rejected.json()["email_error"].lower()

            pending = owner.get(f"/rooms/{room_id}/invites", headers=_auth("owner"))
            assert pending.json() == [
                {"email": "bob@example.test", "role": "viewer"},
                {"email": "carol@example.test", "role": "editor"},
            ]

            bob_joined = owner.post(
                f"/rooms/{room_id}/join", json={}, headers=_auth("bob", "bob@example.test")
            )
            carol_joined = owner.post(
                f"/rooms/{room_id}/join", json={}, headers=_auth("carol", "carol@example.test")
            )
            assert bob_joined.status_code == carol_joined.status_code == 200
            room = owner.get(f"/rooms/{room_id}", headers=_auth("owner")).json()
            permissions = {member["user_id"]: member["permission"] for member in room["members"]}
            assert permissions["bob"] == "viewer"
            assert permissions["carol"] == "editor"
            assert owner.get(f"/rooms/{room_id}/invites", headers=_auth("owner")).json() == []

        # The actual backend made both outbound HTTP requests; delivery failure did not lose the invite.
        assert len(provider_requests) == 2
        for request in provider_requests:
            assert request["path"].startswith("/auth/v1/invite?")
            assert request["headers"]["Authorization"] == f"Bearer {SERVICE_KEY}"
            redirect = parse_qs(urlparse(request["path"]).query)["redirect_to"][0]
            parsed = urlparse(redirect)
            assert f"{parsed.scheme}://{parsed.netloc}" == app_url
            assert parsed.path == "/auth/callback"
        assert [request["body"]["email"] for request in provider_requests] == [
            "bob@example.test", "carol@example.test"
        ]
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait(timeout=5)
        supabase.shutdown()
        supabase.server_close()
        supabase_thread.join(timeout=2)
