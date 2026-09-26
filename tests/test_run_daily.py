"""End-to-end tests for run-daily.py's failure reporting.

Each test runs the real script as a subprocess against a stub
`ansible-playbook` that prints a colored PLAY RECAP (as ansible does under
ANSIBLE_FORCE_COLOR) and exits non-zero, plus local HTTP servers standing in
for the Discord webhook and the Uptime Kuma push endpoint.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "run-daily.py"

# A recap exactly as ansible colors it: the failed/unreachable counters carry
# their own escape codes, so "unreachable=0    failed=1" never appears as
# plain text in the stream.
_RECAP = (
    "PLAY RECAP *********************************************************************\n"
    "\x1b[0;33mweb01\x1b[0m                      : \x1b[0;32mok=9   \x1b[0m "
    "\x1b[0;33mchanged=3   \x1b[0m unreachable=0    failed=0    skipped=0    rescued=0    ignored=0\n"
    "\x1b[0;31mdb01\x1b[0m                       : \x1b[0;32mok=7   \x1b[0m "
    "\x1b[0;33mchanged=2   \x1b[0m unreachable=0    \x1b[0;31mfailed=1   \x1b[0m "
    "skipped=0    rescued=0    ignored=0\n"
    "\x1b[0;31mbackup01\x1b[0m                   : ok=0    changed=0    "
    "\x1b[1;31munreachable=1   \x1b[0m failed=0    skipped=0    rescued=0    ignored=0\n"
)
_CLEAN_RECAP = (
    "PLAY RECAP *********************************************************************\n"
    "\x1b[0;33mweb01\x1b[0m                      : \x1b[0;32mok=9   \x1b[0m "
    "\x1b[0;33mchanged=3   \x1b[0m unreachable=0    failed=0    skipped=0    rescued=0    ignored=0\n"
)


class _Recorder(BaseHTTPRequestHandler):
    """Records every request. Like Discord's Cloudflare front, it refuses the
    default Python-urllib User-Agent with 403 / error code 1010."""

    requests: list = []

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        ua = self.headers.get("User-Agent", "")
        type(self).requests.append(
            {"method": self.command, "path": self.path, "ua": ua, "body": body})
        if ua.startswith("Python-urllib"):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"error code: 1010\n")
            return
        self.send_response(204)
        self.end_headers()

    do_GET = do_POST = _handle

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Recorder.requests = []
    srv = HTTPServer(("127.0.0.1", 0), _Recorder)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _Recorder.requests
    srv.shutdown()


def _run(tmp_path, recap, exit_code, env_extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ansible-playbook"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stdout.write({recap!r})\n"
        f"sys.exit({exit_code})\n")
    stub.chmod(0o755)
    # Never inherit a real webhook or push URL from the developer's shell.
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISCORD_WEBHOOK_URL", "KUMA_PUSH_URL")}
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "ALV_HOME": str(tmp_path),
        "ALV_LOG_DIR": str(tmp_path / "logs"),
        "ALV_CONFIG": str(tmp_path / "absent.toml"),
        "ALV_LOCKFILE": str(tmp_path / "daily.lock"),
        "COLUMNS": "200",
        **env_extra,
    })
    return subprocess.run(
        [sys.executable, str(_SCRIPT)], env=env, capture_output=True, text=True,
        timeout=60)


def test_summary_lists_colored_failed_and_unreachable_hosts(tmp_path):
    proc = _run(tmp_path, _RECAP, 4, {})
    assert proc.returncode == 4
    assert "db01" in proc.stdout and "backup01" in proc.stdout
    assert "1 failed" in proc.stdout
    assert "1 unreachable" in proc.stdout


def test_discord_notification_is_delivered_with_problem_hosts(tmp_path, server):
    base, requests = server
    proc = _run(tmp_path, _RECAP, 4, {"DISCORD_WEBHOOK_URL": f"{base}/webhook"})
    assert proc.returncode == 4
    posts = [r for r in requests if r["path"] == "/webhook"]
    assert len(posts) == 1
    assert not posts[0]["ua"].startswith("Python-urllib")
    content = json.loads(posts[0]["body"])["content"]
    assert "backup01" in content and "db01" in content
    assert "notify failed" not in proc.stderr


def test_undeliverable_discord_notification_is_reported(tmp_path):
    # Nothing listens on the discard port, so delivery fails.
    proc = _run(tmp_path, _RECAP, 4, {"DISCORD_WEBHOOK_URL": "http://127.0.0.1:9/webhook"})
    assert proc.returncode == 4
    assert "notify failed" in proc.stderr


def test_kuma_push_reports_down_with_problem_hosts(tmp_path, server):
    base, requests = server
    proc = _run(tmp_path, _RECAP, 4,
                {"KUMA_PUSH_URL": f"{base}/api/push/tok?status=up&msg=OK&ping="})
    assert proc.returncode == 4
    pushes = [r for r in requests if r["path"].startswith("/api/push/tok")]
    assert len(pushes) == 1
    q = parse_qs(urlparse(pushes[0]["path"]).query)
    assert q["status"] == ["down"]
    assert "backup01" in q["msg"][0] and "db01" in q["msg"][0]


def test_kuma_push_reports_up_on_clean_run(tmp_path, server):
    base, requests = server
    proc = _run(tmp_path, _CLEAN_RECAP, 0,
                {"KUMA_PUSH_URL": f"{base}/api/push/tok?status=up&msg=OK&ping="})
    assert proc.returncode == 0
    pushes = [r for r in requests if r["path"].startswith("/api/push/tok")]
    assert len(pushes) == 1
    assert parse_qs(urlparse(pushes[0]["path"]).query)["status"] == ["up"]
