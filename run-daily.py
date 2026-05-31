#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///

import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# ── paths / config ──────────────────────────────────────────────────────────
# Settings come from a TOML file (default ~/.config/ansible-log-viewer/config.toml,
# overridable with $ALV_CONFIG). Individual paths can also be overridden by the
# ALV_HOME / ALV_LOG_DIR environment variables, which take precedence over the
# file — handy for one-off runs and for pointing at a fixture.
_SCRIPT_DIR = Path(__file__).parent.resolve()
_CONFIG_PATH = (
    Path(os.environ["ALV_CONFIG"]).expanduser()
    if os.environ.get("ALV_CONFIG")
    else Path.home() / ".config" / "ansible-log-viewer" / "config.toml"
)


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


_cfg = _load_config()


def _resolve_path(env_var: str, cfg_key: str, default: Path) -> Path:
    if os.environ.get(env_var):
        return Path(os.environ[env_var]).expanduser()
    if cfg_key in _cfg:
        return Path(_cfg[cfg_key]).expanduser()
    return default


# Root of your Ansible repo (where playbooks/ and inventory/ live). Defaults to
# the current directory, so running the script from your repo root just works.
ANSIBLE_HOME = _resolve_path("ALV_HOME", "ansible_home", Path.cwd())
LOG_DIR = _resolve_path("ALV_LOG_DIR", "log_dir", Path.home() / ".ansible" / "logs")

# Which playbook the daily run executes (relative to ANSIBLE_HOME or absolute),
# and an optional inventory pattern passed to --limit. An empty LIMIT means no
# --limit at all (every host in the inventory).
PLAYBOOK = _cfg.get("playbook", "playbooks/daily.yml")
LIMIT = _cfg.get("limit", "")

LOCKFILE = Path("/tmp/ansible-daily.lock")
MAX_LOG_DAYS = 30

console = Console()


# ── discord notify ────────────────────────────────────────────────────────────
# Optional: set $DISCORD_WEBHOOK_URL or [notify] discord_webhook_url in config to
# get a Discord ping when a run fails. Leave both unset to disable notifications.
_NOTIFY = _cfg.get("notify", {}) if isinstance(_cfg.get("notify"), dict) else {}
NOTIFY_NAME = _NOTIFY.get("name", "ansible-daily")


def _discord_webhook() -> str | None:
    """Webhook URL from $DISCORD_WEBHOOK_URL (preferred) or [notify]
    discord_webhook_url in config.toml. Returns None when unset, which disables
    notifications — a notify problem must never break or mask the run."""
    env = os.environ.get("DISCORD_WEBHOOK_URL")
    if env and env.strip():
        return env.strip()
    url = _NOTIFY.get("discord_webhook_url")
    return url.strip() if isinstance(url, str) and url.strip() else None


def _notify_failure(webhook, failed_hosts, counts, duration, code, logp):
    """POST a concise failure summary to Discord. Best-effort: never raises."""
    ok_c, ch_c, fa_c, un_c = counts
    parts = []
    if ok_c: parts.append(f"{ok_c} ok")
    if ch_c: parts.append(f"{ch_c} changed")
    if fa_c: parts.append(f"{fa_c} failed")
    if un_c: parts.append(f"{un_c} unreachable")
    summary = ", ".join(parts) if parts else "no PLAY RECAP (ansible crashed?)"
    body = (f":x: **{NOTIFY_NAME} run failed**\n"
            f"exit={code} · {duration} · {summary}")
    if failed_hosts:
        body += "\n**Problem hosts:** " + ", ".join(sorted(failed_hosts)[:40])
    body += f"\n`{logp}`"
    payload = json.dumps({"username": NOTIFY_NAME, "content": body}).encode()
    try:
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

# ── lock ──────────────────────────────────────────────────────────────────────
lock_fh = open(LOCKFILE, "w")
try:
    fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    console.print(f"[bold red]Already running[/] — lock held at {LOCKFILE}")
    sys.exit(1)

# ── log files ─────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_path = LOG_DIR / f"daily-{timestamp}.log"
err_path = LOG_DIR / f"daily-{timestamp}.err"

# ── run ───────────────────────────────────────────────────────────────────────
start = datetime.now(timezone.utc)
start_display = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

console.print(f"[bold]Daily run started[/] — {start_display}")
console.print(f"[dim]log:    {log_path}[/]")
console.print(f"[dim]stderr: {err_path}[/]")
console.print()

stdout_lines: list[str] = []

# Only PLAY RECAP rows are needed for the summary table. Accumulating the full
# stdout stream in RAM turned a large (multi-GB) log into an unkillable run, so
# keep just recap-looking lines regardless of how much a play prints.
_RECAP_LINE_RE = re.compile(r"unreachable=\d+\s+failed=\d+")

def _stream(src, dst_file, dst_stream, accumulate=None):
    for raw in src:
        line = raw.decode("utf-8", errors="replace")
        dst_file.write(line)
        if accumulate is not None and _RECAP_LINE_RE.search(line):
            accumulate.append(line)
        try:
            dst_stream.write(line)
            dst_stream.flush()
        except (OSError, BrokenPipeError):
            pass

ansible_cmd = ["ansible-playbook", PLAYBOOK]
if LIMIT:
    ansible_cmd += ["--limit", LIMIT]

with open(log_path, "w") as log_fh, open(err_path, "w") as err_fh:
    proc = subprocess.Popen(
        ansible_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "ANSIBLE_FORCE_COLOR": "1"},
        cwd=ANSIBLE_HOME,
    )

    t_out = threading.Thread(target=_stream, args=(proc.stdout, log_fh, sys.stdout, stdout_lines))
    t_err = threading.Thread(target=_stream, args=(proc.stderr, err_fh, sys.stderr))
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join()
    t_err.join()

exit_code = proc.returncode

elapsed = (datetime.now(timezone.utc) - start).total_seconds()
duration = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

# ── log rotation ──────────────────────────────────────────────────────────────
cutoff = start.timestamp() - MAX_LOG_DAYS * 86400
for pattern in ("daily-*.log", "daily-*.err"):
    for p in LOG_DIR.glob(pattern):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)

# ── parse PLAY RECAP ──────────────────────────────────────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
RECAP_RE = re.compile(
    r"^(\S+)\s+: ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)",
    re.MULTILINE,
)
clean_stdout = ANSI_RE.sub("", "".join(stdout_lines))
hosts = RECAP_RE.findall(clean_stdout)

# ── summary table ─────────────────────────────────────────────────────────────
console.print()

table = Table(box=box.ROUNDED, show_footer=False)
table.add_column("Host", style="dim", no_wrap=True)
table.add_column("Status", no_wrap=True)
table.add_column("ok", justify="right")
table.add_column("changed", justify="right")
table.add_column("fail", justify="right")

total = ok_count = changed_count = failed_count = unreach_count = 0
failed_hosts: list[str] = []

for host, ok, changed, unreach, failed in hosts:
    ok, changed, unreach, failed = int(ok), int(changed), int(unreach), int(failed)
    total += 1

    if unreach > 0:
        status = "[bold red]✗ unreachable[/]"
        unreach_count += 1
        failed_hosts.append(host)
    elif failed > 0:
        status = "[bold red]✗ failed[/]"
        failed_count += 1
        failed_hosts.append(host)
    elif changed > 0:
        status = "[yellow]~ changed[/]"
        changed_count += 1
    else:
        status = "[green]✓ ok[/]"
        ok_count += 1

    table.add_row(host, status, str(ok), str(changed) if changed else "-", str(failed) if failed else "-")

if not hosts:
    table.add_row("[red]no PLAY RECAP found — ansible may have crashed[/]", "", "", "", "")

# Footer row with counts
parts = []
if ok_count:      parts.append(f"[green]{ok_count} ok[/]")
if changed_count: parts.append(f"[yellow]{changed_count} changed[/]")
if failed_count:  parts.append(f"[red]{failed_count} failed[/]")
if unreach_count: parts.append(f"[red]{unreach_count} unreachable[/]")
footer_counts = "  ".join(parts) if parts else "[dim]no hosts[/]"

log_label = f"[dim]log: {log_path}"
if err_path.stat().st_size > 0:
    log_label += f"   stderr: {err_path}"
log_label += "[/]"

title = f"[bold]Daily Run[/]  [dim]{start_display}[/]"
subtitle = f"[dim]{duration}[/]   {footer_counts}   {log_label}"

console.print(Panel(table, title=title, subtitle=subtitle, border_style="bright_black"))

# ── notify on failure ─────────────────────────────────────────────────────────
# Failure = ansible non-zero exit, any failed/unreachable host, or no recap
# at all (crash). Success stays silent to avoid daily noise.
run_failed = (exit_code != 0) or failed_count or unreach_count or not hosts
if run_failed:
    webhook = _discord_webhook()
    if webhook:
        _notify_failure(
            webhook, failed_hosts,
            (ok_count, changed_count, failed_count, unreach_count),
            duration, exit_code, log_path)
    else:
        console.print("[yellow]run failed but no Discord webhook available[/]")

sys.exit(exit_code)
