"""Real Uvicorn + HTTP + SQLite + private originals, with a process restart.

Run from repo root: python tests/integration/package5_http_smoke.py
No AI calls, no external services, no modification of project demo data.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "SAMPLE-only-password-123"


def main():
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    sample = (ROOT / "demo/documents/good_passport.pdf").read_bytes()
    with tempfile.TemporaryDirectory(prefix="visapilot-archive-smoke-") as directory:
        data_dir = Path(directory) / "data"
        environment = {**os.environ,
            "VISAPILOT_DATA_DIR": str(data_dir),
            "VISAPILOT_ALLOWED_ORIGINS": json.dumps([origin]),
            "VISAPILOT_COOKIE_SECURE": "false",
        }
        log = open(Path(directory) / "server.log", "wb")
        process = None

        def stop():
            nonlocal process
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process = None

        def start():
            nonlocal process
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.documents.application:create_app",
                 "--factory", "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log"],
                cwd=ROOT, env=environment, stdout=log, stderr=log,
            )
            deadline = time.monotonic() + 10
            with httpx.Client(trust_env=False, timeout=0.5) as probe:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Smoke server exited before startup")
                    try:
                        if probe.get(origin + "/api/v1/health").status_code == 200:
                            return
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
            raise RuntimeError("Smoke server did not become ready")

        try:
            start()
            with httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, trust_env=False) as alice, \
                    httpx.Client(base_url=origin + "/api/v1", headers={"Origin": origin}, trust_env=False) as bob:
                def sign_in(client, username, register=True):
                    body = {"username": username, "password": PASSWORD}
                    if register:
                        assert client.post("/auth/register", json=body).status_code == 201
                    result = client.post("/auth/login", json=body)
                    assert result.status_code == 200
                    return {"X-CSRF-Token": result.json()["csrf_token"]}

                ah, bh = sign_in(alice, "sample_alice"), sign_in(bob, "sample_bob")
                upload = alice.post("/documents", headers=ah,
                                    files={"file": ("SAMPLE.pdf", sample, "application/pdf")},
                                    data={"document_type": "passport"})
                assert upload.status_code == 201
                identifier = upload.json()["id"]
                path = f"/documents/{identifier}"
                assert alice.get(path + "/file").content == sample
                assert bob.get(path + "/file").status_code == 404
                assert bob.post(path + "/extract", headers=bh).status_code == 404
                assert bob.get("/documents").json() == {"items": []}
                extracted = alice.post(path + "/extract", headers=ah)
                assert extracted.status_code == 200
                document = extracted.json()
                assert document["extraction_state"] == "extracted" and len(document["fields"]) == 6
                confirmed = alice.patch(path + "/fields", headers=ah, json={
                    "expected_facts_revision": document["facts_revision"], "fields": [
                        {"key": f["key"], "value": f["value"], "user_confirmed": True} for f in document["fields"]
                    ],
                })
                assert confirmed.status_code == 200
                stop()
                start()
                assert alice.get("/auth/me").status_code == 200
                assert alice.get(path).json() == confirmed.json()
                assert alice.get(path + "/file").content == sample
                assert alice.post("/auth/logout", headers=ah).status_code == 204
                assert alice.get(path + "/file").status_code == 401
                ah = sign_in(alice, "sample_alice", register=False)
                assert alice.get(path + "/file").content == sample
                deleted = alice.delete(path, headers=ah)
                assert deleted.status_code == 204 and not deleted.content
                assert alice.get(path).status_code == 404
                assert alice.get(path + "/file").status_code == 404
                assert not list((data_dir / "uploads").iterdir())
                assert not list((data_dir / "tmp").iterdir())
            print(json.dumps({"status": "ok", "checks": [
                "two real accounts", "upload/download", "foreign access denied", "PDF extraction",
                "confirmation", "Uvicorn restart", "session and archive persistence", "logout/login", "physical deletion",
            ], "live_ai": "not_run"}))
        finally:
            stop()
            log.close()


if __name__ == "__main__":
    main()
