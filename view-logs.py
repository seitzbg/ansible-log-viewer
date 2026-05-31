#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual", "rich"]
# ///

from __future__ import annotations

import os
import re
import statistics
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.command import CommandPalette
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

# ── config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = (
    Path(os.environ["ALV_CONFIG"]).expanduser()
    if os.environ.get("ALV_CONFIG")
    else Path.home() / ".config" / "ansible-log-viewer" / "config.toml"
)
_SCRIPT_DIR = Path(__file__).parent.resolve()
# Default to the current directory so the viewer finds a facts-snapshot/ dir in
# the Ansible repo you run it from; override with `ansible_home` in config or
# the ALV_HOME env var.
_DEFAULT_ANSIBLE_HOME = Path.cwd()
_DEFAULT_CONFIG = f"""\
# Ansible Log Viewer settings

# Root of your Ansible repo — used by run-daily.py as the working directory
# so that playbooks/ and inventory/ are resolved correctly.
ansible_home = "{_DEFAULT_ANSIBLE_HOME}"

# Available themes:
#   catppuccin-mocha  (dark)
#   tokyonight-night  (dark)
#   oxocarbon-dark    (dark)
#   moonfly           (dark)
#   vision            (dark)
#   cyberpunk-2077    (dark)
theme = "catppuccin-mocha"

# Override log directory (default: ~/.ansible/logs)
# log_dir = "~/.ansible/logs"
"""

@dataclass
class AppConfig:
    theme: str = "catppuccin-mocha"
    log_dir: Path = field(default_factory=lambda: Path.home() / ".ansible" / "logs")
    ansible_home: Path = field(default_factory=lambda: _DEFAULT_ANSIBLE_HOME)


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """ALV_LOG_DIR / ALV_HOME / ALV_THEME override the config file. Useful for
    one-off runs and for pointing the viewer at a fixture without editing
    config.toml."""
    if os.environ.get("ALV_LOG_DIR"):
        cfg.log_dir = Path(os.environ["ALV_LOG_DIR"]).expanduser()
    if os.environ.get("ALV_HOME"):
        cfg.ansible_home = Path(os.environ["ALV_HOME"]).expanduser()
    if os.environ.get("ALV_THEME"):
        cfg.theme = os.environ["ALV_THEME"]
    return cfg


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(_DEFAULT_CONFIG)
        return _apply_env_overrides(AppConfig())
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    return _apply_env_overrides(AppConfig(
        theme=raw.get("theme", "catppuccin-mocha"),
        log_dir=Path(raw["log_dir"]).expanduser() if "log_dir" in raw else Path.home() / ".ansible" / "logs",
        ansible_home=Path(raw["ansible_home"]).expanduser() if "ansible_home" in raw else _DEFAULT_ANSIBLE_HOME,
    ))


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    default_log_dir = Path.home() / ".ansible" / "logs"
    log_dir_line = "" if config.log_dir == default_log_dir else f'\nlog_dir = "{config.log_dir}"\n'
    CONFIG_PATH.write_text(
        "# Ansible Log Viewer settings\n\n"
        "# Root of your Ansible repo\n"
        f'ansible_home = "{config.ansible_home}"\n'
        "\n# Available themes:\n"
        "#   catppuccin-mocha  (dark)\n"
        "#   tokyonight-night  (dark)\n"
        "#   oxocarbon-dark    (dark)\n"
        "#   moonfly           (dark)\n"
        "#   vision            (dark)\n"
        "#   cyberpunk-2077    (dark)\n"
        f'theme = "{config.theme}"\n'
        "\n# Override log directory (default: ~/.ansible/logs)\n"
        "# log_dir = \"~/.ansible/logs\"\n"
        f"{log_dir_line}"
    )


# ── themes ────────────────────────────────────────────────────────────────────

# Per-theme semantic colors for Rich text markup and DataTable cells.
_THEME_COLORS: dict[str, dict[str, str]] = {
    "catppuccin-mocha": {
        "ok": "#a6e3a1", "changed": "#f9e2af", "failed": "#f38ba8",
        "unreachable": "#eba0ac", "dim": "#585b70", "accent": "#b4befe",
    },
    "tokyonight-night": {
        "ok": "#9ece6a", "changed": "#e0af68", "failed": "#f7768e",
        "unreachable": "#ff9e64", "dim": "#3b3560", "accent": "#9580ff",
    },
    "oxocarbon-dark": {
        "ok": "#42be65", "changed": "#08bdba", "failed": "#ee5396",
        "unreachable": "#ff7eb6", "dim": "#393939", "accent": "#78a9ff",
    },
    "moonfly": {
        "ok": "#36c692", "changed": "#e3c78a", "failed": "#ff5d5d",
        "unreachable": "#ff5189", "dim": "#626262", "accent": "#80a0ff",
    },
    "vision": {
        "ok": "#9ece6a", "changed": "#e0af68", "failed": "#f7768e",
        "unreachable": "#ff9e64", "dim": "#414868", "accent": "#7dcfff",
    },
    "cyberpunk-2077": {
        "ok": "#00ff9c", "changed": "#fffc58", "failed": "#ff1865",
        "unreachable": "#6766b3", "dim": "#4a4870", "accent": "#fee801",
    },
}

_THEMES: dict[str, Theme] = {
    "catppuccin-mocha": Theme(
        name="catppuccin-mocha", dark=True,
        primary="#89b4fa", secondary="#cba6f7", accent="#b4befe",
        foreground="#cdd6f4", background="#1e1e2e", surface="#181825",
        panel="#313244", boost="#45475a",
        success="#a6e3a1", warning="#f9e2af", error="#f38ba8",
    ),
    "tokyonight-night": Theme(
        name="tokyonight-night", dark=True,
        primary="#9580ff", secondary="#bb9af7", accent="#2ac3de",
        foreground="#c0caf5", background="#1a1826", surface="#13111e",
        panel="#252040", boost="#2e2a4a",
        success="#9ece6a", warning="#e0af68", error="#f7768e",
    ),
    "oxocarbon-dark": Theme(
        name="oxocarbon-dark", dark=True,
        primary="#78a9ff", secondary="#be95ff", accent="#33b1ff",
        foreground="#e0e0e0", background="#161616", surface="#1c1c1c",
        panel="#262626", boost="#393939",
        success="#42be65", warning="#08bdba", error="#ee5396",
    ),
    "moonfly": Theme(
        name="moonfly", dark=True,
        primary="#80a0ff", secondary="#cf87e8", accent="#79dac8",
        foreground="#c6c6c6", background="#080808", surface="#121212",
        panel="#262626", boost="#303030",
        success="#36c692", warning="#e3c78a", error="#ff5d5d",
    ),
    "vision": Theme(
        name="vision", dark=True,
        primary="#7dcfff", secondary="#bb9af7", accent="#c3e88d",
        foreground="#eeffee", background="#000022", surface="#05051a",
        panel="#1a2040", boost="#252d52",
        success="#9ece6a", warning="#e0af68", error="#f7768e",
    ),
    "cyberpunk-2077": Theme(
        name="cyberpunk-2077", dark=True,
        primary="#fee801", secondary="#02d7f2", accent="#ff2daa",
        foreground="#f0f0f0", background="#0b0b0f", surface="#101011",
        panel="#1c1c24", boost="#28283a",
        success="#00ff9c", warning="#fffc58", error="#ff1865",
    ),
}

THEME_DESCRIPTIONS: dict[str, str] = {
    "catppuccin-mocha":  "Catppuccin Mocha  (dark)",
    "tokyonight-night":  "Tokyo Night       (dark)",
    "oxocarbon-dark":    "Oxocarbon         (dark)",
    "moonfly":           "Moonfly           (dark)",
    "vision":            "Vision            (dark)",
    "cyberpunk-2077":    "Cyberpunk 2077    (dark)",
}

# ── constants ─────────────────────────────────────────────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
FNAME_RE = re.compile(r"daily-(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})\.log$")
RECAP_RE = re.compile(
    r"^(\S+)\s+: ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)"
    r"\s+skipped=(\d+)\s+rescued=(\d+)\s+ignored=(\d+)",
    re.MULTILINE,
)
TASK_LINE_RE = re.compile(r"^(.+?)\s+-{10,}\s+([\d.]+)s\s*$")
ELAPSED_RE = re.compile(r"\([\d:\.]+\)\s+([\d]+:[\d]{2}:[\d]{2}\.[\d]+)\s+\*+")
STATUS_ICON = {"success": "✓", "partial": "~", "failed": "✗", "empty": "?"}
FILTER_MODES = ["all", "failed", "unreachable", "changed", "ok"]

_config = load_config()
LOG_DIR = _config.log_dir
_colors = _THEME_COLORS.get(_config.theme, _THEME_COLORS["catppuccin-mocha"])
STATUS_COLOR = {"success": _colors["ok"], "partial": _colors["changed"], "failed": _colors["failed"], "empty": _colors["dim"]}
HOST_STATUS_COLOR = {"ok": _colors["ok"], "changed": _colors["changed"], "failed": _colors["failed"], "unreachable": _colors["unreachable"]}


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class HostStat:
    hostname: str
    ok: int
    changed: int
    unreachable: int
    failed: int
    skipped: int
    rescued: int
    ignored: int

    @property
    def status(self) -> str:
        if self.unreachable > 0:
            return "unreachable"
        if self.failed > 0:
            return "failed"
        if self.changed > 0:
            return "changed"
        return "ok"

    @property
    def severity(self) -> int:
        return {"unreachable": 0, "failed": 1, "changed": 2, "ok": 3}[self.status]


@dataclass
class TaskStat:
    name: str
    duration_s: float


@dataclass
class FactsMirror:
    """Outcome of the `facts.py mirror` play in gather-all-facts.yml.

    `present` is False for logs predating that play. `degraded` is True when
    the fact store did not fully update even if PLAY RECAP looks green —
    used to downgrade an otherwise-successful run to "partial".
    """
    present: bool = False
    mirrored: Optional[int] = None
    snapshot_dir: Optional[str] = None
    skipped: int = 0
    pruned_snapshots: int = 0
    pruned_redis: int = 0
    redis_ok: bool = True
    command_failed: bool = False
    fail_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.present and (
            self.command_failed or not self.redis_ok or bool(self.warnings)
        )


@dataclass
class RunData:
    timestamp: datetime
    log_path: Path
    err_path: Optional[Path]
    duration: Optional[str]
    duration_s: Optional[float]
    status: str
    hosts: list[HostStat] = field(default_factory=list)
    tasks: list[TaskStat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts: FactsMirror = field(default_factory=FactsMirror)
    total_hosts: int = 0
    ok_hosts: int = 0
    changed_hosts: int = 0
    failed_hosts: int = 0
    unreachable_hosts: int = 0
    warning_count: int = 0


# ── parser ────────────────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def scan_log_dir() -> list[tuple[Path, Optional[Path]]]:
    logs = sorted(LOG_DIR.glob("daily-*.log"), reverse=True)
    pairs = []
    for log in logs:
        err = log.with_suffix(".err")
        pairs.append((log, err if err.exists() else None))
    return pairs


def parse_timestamp(log_path: Path) -> datetime:
    m = FNAME_RE.match(log_path.name)
    if not m:
        return datetime.fromtimestamp(log_path.stat().st_mtime)
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H-%M-%S")


def parse_play_recap(clean: str) -> list[HostStat]:
    start = clean.find("PLAY RECAP")
    if start == -1:
        return []
    end = clean.find("TASKS RECAP", start)
    section = clean[start:] if end == -1 else clean[start:end]
    hosts = []
    for m in RECAP_RE.finditer(section):
        hosts.append(HostStat(
            hostname=m.group(1),
            ok=int(m.group(2)),
            changed=int(m.group(3)),
            unreachable=int(m.group(4)),
            failed=int(m.group(5)),
            skipped=int(m.group(6)),
            rescued=int(m.group(7)),
            ignored=int(m.group(8)),
        ))
    return hosts


def parse_tasks_recap(clean: str) -> tuple[Optional[str], Optional[float], list[TaskStat]]:
    idx = clean.find("TASKS RECAP")
    if idx == -1:
        return None, None, []
    lines = clean[idx:].splitlines()
    duration_str: Optional[str] = None
    duration_s: Optional[float] = None
    if len(lines) > 1:
        m = ELAPSED_RE.search(lines[1])
        if m:
            duration_str = m.group(1)
            h, mi, s = duration_str.split(":")
            duration_s = int(h) * 3600 + int(mi) * 60 + float(s)
    tasks = []
    for line in lines[3:]:
        m = TASK_LINE_RE.match(line.strip())
        if m:
            tasks.append(TaskStat(name=m.group(1).strip(), duration_s=float(m.group(2))))
    tasks.sort(key=lambda t: t.duration_s, reverse=True)
    return duration_str, duration_s, tasks


def parse_warnings(err_path: Optional[Path]) -> list[str]:
    if err_path is None or not err_path.exists() or err_path.stat().st_size == 0:
        return []
    text = err_path.read_text(errors="replace")
    lines = []
    for line in strip_ansi(text).splitlines():
        line = re.sub(r"^\[.*?\]:\s*", "", line.strip())
        if line:
            lines.append(line)
    return lines


_MIRROR_TASK = "TASK [Run facts.py mirror"
_FM_MIRRORED_RE = re.compile(
    r"mirrored (\d+) hosts to redis(?: \+ ([^\s\"]+))?(?:; pruned (\d+) stale redis keys)?"
)
_FM_SKIPPED_RE = re.compile(r"skipped (\d+) cache entries without ansible_distribution")
_FM_PRUNED_SNAP_RE = re.compile(r"pruned (\d+) superseded short-name")
_FM_WARN_CREDS_RE = re.compile(r"WARN: redis creds unavailable[^\"\n]*")
_FM_WARN_FAIL_RE = re.compile(r"WARN: redis mirror failed \((.*?)\); snapshots still written")
_FM_FATAL_RE = re.compile(r'fatal: \[[^\]]+\][^\n]*facts\.py[^\n]*"mirror"[^\n]*')
_FM_MSG_RE = re.compile(r'"msg":\s*"([^"]+)"')
_FM_ERR_RE = re.compile(r"\w*Error: [^\"\\]+")


def parse_facts_mirror(clean: str) -> FactsMirror:
    fm = FactsMirror(present=_MIRROR_TASK in clean)
    if not fm.present:
        return fm

    m = _FM_MIRRORED_RE.search(clean)
    if m:
        fm.mirrored = int(m.group(1))
        fm.snapshot_dir = m.group(2)
        if m.group(3):
            fm.pruned_redis = int(m.group(3))
    if (m := _FM_SKIPPED_RE.search(clean)):
        fm.skipped = int(m.group(1))
    if (m := _FM_PRUNED_SNAP_RE.search(clean)):
        fm.pruned_snapshots = int(m.group(1))

    if (m := _FM_WARN_CREDS_RE.search(clean)):
        fm.redis_ok = False
        fm.warnings.append(m.group(0).strip())
    if (m := _FM_WARN_FAIL_RE.search(clean)):
        fm.redis_ok = False
        fm.warnings.append(m.group(0).strip())

    if (m := _FM_FATAL_RE.search(clean)):
        fm.command_failed = True
        line = m.group(0)
        parts = []
        if (mm := _FM_MSG_RE.search(line)):
            parts.append(mm.group(1))
        errs = _FM_ERR_RE.findall(line)
        if errs:
            parts.append(errs[-1].strip())
        fm.fail_reason = " — ".join(parts) if parts else "mirror command failed"
    return fm


# ── fact snapshots ────────────────────────────────────────────────────────────
# tools/facts.py writes curated `facts-snapshot/<host>.yml` files (simple
# key: value scalars, FQDN-canonical with short-name aliases also present).
# We read them directly — no PyYAML, no redis, no network — to enrich the
# Hosts table with current distro/kernel/cpu/ram/ip per host.

_SNAPSHOT_LINE_RE = re.compile(r"^([\w.]+):\s?(.*)$")


def _parse_snapshot(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _SNAPSHOT_LINE_RE.match(line)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[m.group(1)] = val
    return out


def load_fact_snapshots() -> dict[str, dict[str, str]]:
    snap_dir = _config.ansible_home / "facts-snapshot"
    snaps: dict[str, dict[str, str]] = {}
    if not snap_dir.is_dir():
        return snaps
    for f in snap_dir.glob("*.yml"):
        try:
            snaps[f.stem] = _parse_snapshot(f.read_text(errors="replace"))
        except OSError:
            continue
    return snaps


_FACT_CACHE: Optional[dict[str, dict[str, str]]] = None


def facts_for(hostname: str) -> dict[str, str]:
    """Resolve a PLAY RECAP host name to its snapshot, tolerating the
    short-alias ↔ FQDN split (recap may say `web01`, snapshot is
    `web01.example.net.yml`, or vice versa)."""
    global _FACT_CACHE
    if _FACT_CACHE is None:
        _FACT_CACHE = load_fact_snapshots()
    if hostname in _FACT_CACHE:
        return _FACT_CACHE[hostname]
    prefix = hostname + "."
    for name in sorted(_FACT_CACHE):
        if name.startswith(prefix):
            return _FACT_CACHE[name]
    return {}


def determine_status(hosts: list[HostStat]) -> str:
    if not hosts:
        return "empty"
    total_bad = sum(h.failed + h.unreachable for h in hosts)
    total_good = sum(h.ok + h.changed for h in hosts)
    if total_bad == 0:
        return "success"
    if total_good == 0:
        return "failed"
    return "partial"


def parse_run(log_path: Path, err_path: Optional[Path]) -> RunData:
    timestamp = parse_timestamp(log_path)
    if log_path.stat().st_size == 0:
        return RunData(
            timestamp=timestamp, log_path=log_path, err_path=err_path,
            duration=None, duration_s=None, status="empty",
        )
    content = log_path.read_text(errors="replace")
    clean = strip_ansi(content)
    hosts = parse_play_recap(clean)
    duration_str, duration_s, tasks = parse_tasks_recap(clean)
    warnings = parse_warnings(err_path)
    facts = parse_facts_mirror(clean)
    status = determine_status(hosts)
    if status == "success" and facts.degraded:
        status = "partial"
    run = RunData(
        timestamp=timestamp, log_path=log_path, err_path=err_path,
        duration=duration_str, duration_s=duration_s, status=status,
        hosts=hosts, tasks=tasks, warnings=warnings, facts=facts,
    )
    run.total_hosts = len(hosts)
    run.ok_hosts = sum(1 for h in hosts if h.status == "ok")
    run.changed_hosts = sum(1 for h in hosts if h.status == "changed")
    run.failed_hosts = sum(1 for h in hosts if h.status == "failed")
    run.unreachable_hosts = sum(1 for h in hosts if h.status == "unreachable")
    run.warning_count = len(warnings)
    return run


def parse_all_runs() -> list[RunData]:
    return [parse_run(log, err) for log, err in scan_log_dir()]


# ── helper renderers ──────────────────────────────────────────────────────────

def duration_bar(duration_s: float, max_s: float, width: int = 20) -> Text:
    if max_s == 0:
        return Text("")
    count = max(1, round((duration_s / max_s) * width))
    bar = "█" * count
    frac = duration_s / max_s
    color = _colors["failed"] if frac > 0.66 else _colors["changed"] if frac > 0.33 else _colors["ok"]
    return Text(bar, style=color)


def trend_line(runs: list[RunData]) -> str:
    parts = []
    for r in reversed(runs):
        icon = STATUS_ICON[r.status]
        color = STATUS_COLOR[r.status]
        parts.append(f"[{color}]{icon}[/{color}]")
    return " ".join(parts)


def fmt_duration(s: Optional[float]) -> str:
    if s is None:
        return "—"
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s"


# ── widgets ───────────────────────────────────────────────────────────────────

class AllRunsItem(ListItem):
    def compose(self) -> ComposeResult:
        c = _colors["accent"]
        yield Label(f"[bold][{c}]All Runs[/{c}][/bold]")


class RunListItem(ListItem):
    def __init__(self, run: RunData) -> None:
        super().__init__()
        self.run = run

    def compose(self) -> ComposeResult:
        icon = STATUS_ICON[self.run.status]
        color = STATUS_COLOR[self.run.status]
        ts = self.run.timestamp.strftime("%m-%d %H:%M")
        dur = self.run.duration or "—"
        hosts = f"{self.run.total_hosts}h" if self.run.total_hosts else ""
        d = _colors["dim"]
        yield Label(f"[{color}]{icon}[/{color}] {ts}  [{d}]{hosts} {dur}[/{d}]")


class RunListView(ListView):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]


class SummaryTab(Static):
    @staticmethod
    def _facts_lines(run: RunData) -> list[str]:
        fm = run.facts
        ok, chg, fail, dim = (
            _colors["ok"], _colors["changed"], _colors["failed"], _colors["dim"]
        )
        if not fm.present:
            return [f"  [{dim}]not run[/{dim}]"]
        if fm.command_failed:
            out = [f"  [{fail}]✗ command failed[/{fail}]  {fm.fail_reason or ''}"]
        elif not fm.redis_ok:
            out = [f"  [{chg}]~ snapshots written, redis skipped[/{chg}]"]
            out += [f"  [{dim}]{w}[/{dim}]" for w in fm.warnings]
        else:
            mirrored = "—" if fm.mirrored is None else str(fm.mirrored)
            out = [f"  [{ok}]✓ {mirrored} hosts → redis + snapshots[/{ok}]"]
            out.append(
                f"  [{dim}]skipped {fm.skipped} ghost · "
                f"pruned {fm.pruned_snapshots} snap / {fm.pruned_redis} redis[/{dim}]"
            )
        if run.status == "partial" and fm.degraded and run.failed_hosts == 0 \
                and run.unreachable_hosts == 0:
            out.append(f"  [{dim}]status partial: facts mirror degraded[/{dim}]")
        return out

    def show_run(self, run: RunData, prev: Optional[RunData]) -> None:
        icon = STATUS_ICON[run.status]
        color = STATUS_COLOR[run.status]
        ts = run.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        dur = run.duration or "—"

        lines = [
            f"[bold]Status:[/bold]   [{color}]{icon} {run.status.upper()}[/{color}]",
            f"[bold]Time:[/bold]     {ts}",
            f"[bold]Duration:[/bold] {dur}",
            "",
            f"[bold]Hosts:[/bold]    {run.total_hosts} total",
            f"  [{_colors['ok']}]✓ ok[/{_colors['ok']}]           {run.ok_hosts}",
            f"  [{_colors['changed']}]~ changed[/{_colors['changed']}]      {run.changed_hosts}",
            f"  [{_colors['failed']}]✗ failed[/{_colors['failed']}]        {run.failed_hosts}",
            f"  [{_colors['unreachable']}]✗ unreachable[/{_colors['unreachable']}]  {run.unreachable_hosts}",
            "",
            f"[bold]Tasks:[/bold]    {len(run.tasks)}",
            f"[bold]Warnings:[/bold] {run.warning_count}",
            "",
            "[bold]Facts Mirror:[/bold]",
        ]
        lines += self._facts_lines(run)

        if prev and prev.status != "empty":
            d_changed = run.changed_hosts - prev.changed_hosts
            d_failed = run.failed_hosts - prev.failed_hosts
            d_unreach = run.unreachable_hosts - prev.unreachable_hosts
            lines += ["", "[bold]vs previous run:[/bold]"]
            for label, delta in [("changed", d_changed), ("failed", d_failed), ("unreachable", d_unreach)]:
                if delta == 0:
                    lines.append(f"  {label}: [dim]no change[/dim]")
                elif delta > 0:
                    lines.append(f"  {label}: [{_colors['failed']}]+{delta}[/{_colors['failed']}]")
                else:
                    lines.append(f"  {label}: [{_colors['ok']}]{delta}[/{_colors['ok']}]")

        self.update("\n".join(lines))

    def show_aggregate(self, runs: list[RunData]) -> None:
        non_empty = [r for r in runs if r.status != "empty"]
        total = len(runs)
        success = sum(1 for r in runs if r.status == "success")
        partial = sum(1 for r in runs if r.status == "partial")
        failed = sum(1 for r in runs if r.status in ("failed", "empty"))
        rate = f"{success / total * 100:.0f}%" if total else "—"
        durations = [r.duration_s for r in non_empty if r.duration_s is not None]
        avg_dur = fmt_duration(statistics.mean(durations)) if durations else "—"
        min_dur = fmt_duration(min(durations)) if durations else "—"
        max_dur = fmt_duration(max(durations)) if durations else "—"

        failure_counts: Counter[str] = Counter()
        for r in runs:
            for h in r.hosts:
                if h.failed > 0 or h.unreachable > 0:
                    failure_counts[h.hostname] += 1

        lines = [
            f"[bold]Total Runs:[/bold]   {total}",
            f"[bold]Success:[/bold]      [{_colors['ok']}]{success}[/{_colors['ok']}]  ({rate})",
            f"[bold]Partial:[/bold]      [{_colors['changed']}]{partial}[/{_colors['changed']}]",
            f"[bold]Failed/Empty:[/bold] [{_colors['failed']}]{failed}[/{_colors['failed']}]",
            "",
            f"[bold]Duration avg:[/bold] {avg_dur}",
            f"[bold]Duration min:[/bold] {min_dur}",
            f"[bold]Duration max:[/bold] {max_dur}",
        ]

        if failure_counts:
            lines += ["", "[bold]Most-Failing Hosts:[/bold]"]
            for host, count in failure_counts.most_common(8):
                lines.append(f"  [{_colors['failed']}]{host}[/{_colors['failed']}]  [dim]{count}x[/dim]")

        lines += ["", "[bold]Run Trend (oldest → newest):[/bold]", "  " + trend_line(runs)]

        self.update("\n".join(lines))


class HostsTab(Static):
    _filter_idx: int = 0
    _run: Optional[RunData] = None

    BINDINGS = [Binding("f", "filter_next", "Filter hosts")]

    def compose(self) -> ComposeResult:
        yield Label(
            "Filter: [bold]all[/bold]  [dim](f to cycle, enter for host log)[/dim]",
            id="hosts-filter-label",
        )
        yield DataTable(id="hosts-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#hosts-table", DataTable)
        table.add_column("Hostname", key="hostname", width=32)
        table.add_column("Status", key="status", width=12)
        table.add_column("ok", key="ok", width=5)
        table.add_column("chg", key="changed", width=5)
        table.add_column("fail", key="failed", width=5)
        table.add_column("unrch", key="unreachable", width=6)
        table.add_column("skip", key="skipped", width=5)
        table.add_column("resc", key="rescued", width=5)
        table.add_column("ign", key="ignored", width=5)
        table.add_column("distro", key="distro", width=12)
        table.add_column("ver", key="ver", width=8)
        table.add_column("kernel", key="kernel", width=24)
        table.add_column("vCPU", key="vcpu", width=5)
        table.add_column("RAM(MB)", key="ram", width=8)
        table.add_column("IP", key="ip", width=16)

    def show_run(self, run: RunData) -> None:
        self._run = run
        self._filter_idx = 0
        self._repopulate()

    def clear(self) -> None:
        self._run = None
        table = self.query_one("#hosts-table", DataTable)
        table.clear()

    def action_filter_next(self) -> None:
        self._filter_idx = (self._filter_idx + 1) % len(FILTER_MODES)
        mode = FILTER_MODES[self._filter_idx]
        self.query_one("#hosts-filter-label", Label).update(
            f"Filter: [bold]{mode}[/bold]  [dim](f to cycle, enter for host log)[/dim]"
        )
        self._repopulate()

    def _repopulate(self) -> None:
        if self._run is None:
            return
        table = self.query_one("#hosts-table", DataTable)
        table.clear()
        mode = FILTER_MODES[self._filter_idx]
        hosts = sorted(self._run.hosts, key=lambda h: (h.severity, h.hostname))
        for h in hosts:
            if mode != "all" and h.status != mode:
                continue
            color = HOST_STATUS_COLOR[h.status]
            f = facts_for(h.hostname)
            dim = _colors["dim"]

            def fact(key: str) -> Text:
                v = f.get(key)
                return Text(v, style=_colors["accent"]) if v else Text("-", style=dim)

            table.add_row(
                Text(h.hostname),
                Text(h.status, style=color),
                Text(str(h.ok), style=_colors["ok"] if h.ok else dim),
                Text(str(h.changed), style=_colors["changed"] if h.changed else dim),
                Text(str(h.failed), style=_colors["failed"] if h.failed else dim),
                Text(str(h.unreachable), style=_colors["unreachable"] if h.unreachable else dim),
                Text(str(h.skipped), style=dim),
                Text(str(h.rescued), style=dim),
                Text(str(h.ignored), style=dim),
                fact("ansible_distribution"),
                fact("ansible_distribution_version"),
                fact("ansible_kernel"),
                fact("ansible_processor_vcpus"),
                fact("ansible_memtotal_mb"),
                fact("primary_ipv4"),
                key=h.hostname,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._run is None or event.row_key.value is None:
            return
        self.app.push_screen(HostLogModal(event.row_key.value, self._run))


class TasksTab(Static):
    def compose(self) -> ComposeResult:
        yield DataTable(id="tasks-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#tasks-table", DataTable)
        table.add_column("Task Name", key="name")
        table.add_column("Duration", key="duration", width=10)
        table.add_column("Bar", key="bar", width=22)

    def show_run(self, run: RunData) -> None:
        table = self.query_one("#tasks-table", DataTable)
        table.clear()
        if not run.tasks:
            return
        max_s = run.tasks[0].duration_s if run.tasks else 1.0
        for t in run.tasks:
            table.add_row(
                Text(t.name),
                Text(f"{t.duration_s:.2f}s"),
                duration_bar(t.duration_s, max_s),
            )

    def clear(self) -> None:
        self.query_one("#tasks-table", DataTable).clear()


class FactsTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("[dim]No run selected[/dim]", id="facts-content-inner")

    def _placeholder(self, text: str) -> None:
        self.query_one("#facts-content-inner", Static).update(f"[dim]{text}[/dim]")

    def clear(self) -> None:
        self._placeholder("Select a run to view details")

    def show_run(self, run: RunData) -> None:
        fm = run.facts
        if not fm.present:
            self._placeholder("Facts mirror play did not run for this log")
            return
        ok, chg, fail, dim, acc = (
            _colors["ok"], _colors["changed"], _colors["failed"],
            _colors["dim"], _colors["accent"],
        )
        if fm.command_failed:
            state = f"[{fail}]✗ command failed[/{fail}]"
        elif not fm.redis_ok:
            state = f"[{chg}]~ degraded (snapshots written, redis skipped)[/{chg}]"
        else:
            state = f"[{ok}]✓ ok[/{ok}]"
        lines = [
            f"[bold][{acc}]Facts Mirror[/{acc}][/bold]",
            "",
            f"[bold]State:[/bold]            {state}",
            f"[bold]Hosts mirrored:[/bold]   {fm.mirrored if fm.mirrored is not None else '—'}",
            f"[bold]Snapshot dir:[/bold]     {fm.snapshot_dir or '—'}",
            f"[bold]Ghost skipped:[/bold]    {fm.skipped}  [{dim}](cache entries without ansible_distribution)[/{dim}]",
            f"[bold]Pruned snapshots:[/bold] {fm.pruned_snapshots}",
            f"[bold]Pruned redis keys:[/bold] {fm.pruned_redis}",
            f"[bold]Redis updated:[/bold]    {'yes' if fm.redis_ok else 'no'}",
        ]
        if fm.fail_reason:
            lines += ["", f"[bold]Failure reason:[/bold]", f"  [{fail}]{fm.fail_reason}[/{fail}]"]
        if fm.warnings:
            lines += ["", "[bold]Warnings:[/bold]"]
            lines += [f"  [{chg}]{w}[/{chg}]" for w in fm.warnings]
        self.query_one("#facts-content-inner", Static).update("\n".join(lines))


class RawLogTab(Vertical):
    _loaded_path: Optional[Path] = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="raw-log", auto_scroll=False, markup=False, highlight=False)

    def load(self, run: RunData) -> None:
        if self._loaded_path == run.log_path:
            return
        self._loaded_path = run.log_path
        log_widget = self.query_one("#raw-log", RichLog)
        log_widget.clear()
        if run.log_path.stat().st_size == 0:
            log_widget.write(Text("(empty log file)", style="dim"))
            return
        for line in run.log_path.read_text(errors="replace").splitlines():
            log_widget.write(Text.from_ansi(line))
        self.call_after_refresh(log_widget.scroll_home, animate=False)

    def reset(self) -> None:
        self._loaded_path = None
        self.query_one("#raw-log", RichLog).clear()


class WarningsTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("[dim]No run selected[/dim]", id="warnings-content")

    def show_run(self, run: RunData) -> None:
        c = _colors["changed"]
        lines = [f"[{c}]{w}[/{c}]" for w in run.warnings]
        content = "\n".join(lines) if lines else "[dim]No warnings or errors[/dim]"
        self.query_one("#warnings-content", Static).update(content)


class RunListPanel(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label(" Runs ", id="runs-label")
        yield RunListView(id="run-list")

    def populate(self, runs: list[RunData]) -> None:
        lv = self.query_one("#run-list", RunListView)
        lv.clear()
        lv.append(AllRunsItem())
        for run in runs:
            lv.append(RunListItem(run))


class ContentPanel(Static):
    def compose(self) -> ComposeResult:
        with TabbedContent(id="tabs"):
            with TabPane("Summary", id="tab-summary"):
                yield SummaryTab(id="summary-content")
            with TabPane("Hosts", id="tab-hosts"):
                yield HostsTab(id="hosts-content")
            with TabPane("Tasks", id="tab-tasks"):
                yield TasksTab(id="tasks-content")
            with TabPane("Facts", id="tab-facts"):
                yield FactsTab(id="facts-content")
            with TabPane("Raw Log", id="tab-raw"):
                yield RawLogTab(id="raw-content")
            with TabPane("Warnings", id="tab-warnings"):
                yield WarningsTab(id="warnings-content-outer")

    def show(self, run: Optional[RunData], all_runs: list[RunData]) -> None:
        summary = self.query_one("#summary-content", SummaryTab)
        hosts = self.query_one("#hosts-content", HostsTab)
        tasks = self.query_one("#tasks-content", TasksTab)
        facts = self.query_one("#facts-content", FactsTab)
        raw = self.query_one("#raw-content", RawLogTab)
        warnings = self.query_one("#warnings-content-outer", WarningsTab)

        if run is None:
            summary.show_aggregate(all_runs)
            hosts.clear()
            tasks.clear()
            facts.clear()
            raw.reset()
            warnings.query_one("#warnings-content", Static).update("[dim]Select a run to view details[/dim]")
        else:
            idx = next((i for i, r in enumerate(all_runs) if r.log_path == run.log_path), -1)
            prev = all_runs[idx + 1] if idx >= 0 and idx + 1 < len(all_runs) else None
            summary.show_run(run, prev)
            hosts.show_run(run)
            tasks.show_run(run)
            facts.show_run(run)
            raw.reset()
            warnings.show_run(run)

        tabs = self.query_one("#tabs", TabbedContent)
        active = tabs.active
        if active == "tab-raw" and run is not None:
            raw.load(run)



# ── host log modal ────────────────────────────────────────────────────────────

class HostLogModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("/", "focus_search", "Search"),
        Binding("ctrl+f", "focus_search", "Search", show=False),
    ]

    def __init__(self, hostname: str, run: RunData) -> None:
        super().__init__()
        self._hostname = hostname
        self._run = run
        self._all_lines: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="host-log-modal"):
            yield Label("", id="host-log-title")
            yield Input(placeholder="search (filters visible lines)…", id="host-log-search")
            yield RichLog(
                id="host-log-content",
                auto_scroll=False,
                markup=False,
                highlight=False,
                wrap=True,
            )

    def on_mount(self) -> None:
        a = _colors["accent"]
        d = _colors["dim"]
        ts = self._run.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        self.query_one("#host-log-title", Label).update(
            f"[bold][{a}]{self._hostname}[/{a}][/bold]  "
            f"[{d}]{ts}  ·  esc close · / search[/{d}]"
        )
        self._all_lines = self._extract_host_lines()
        self._render_lines(self._all_lines)
        # Keep focus on the log so the user can scroll immediately
        self.query_one("#host-log-content", RichLog).focus()

    def _extract_host_lines(self) -> list[str]:
        log_path = self._run.log_path
        if not log_path.exists() or log_path.stat().st_size == 0:
            return []
        host = self._hostname
        bracket = re.compile(r"\[" + re.escape(host) + r"(?:\s*->|\])")
        recap = re.compile(r"^" + re.escape(host) + r"\s*:\s*ok=")
        header = re.compile(r"^(PLAY \[|TASK \[|RUNNING HANDLER \[|PLAY RECAP|TASKS RECAP)")

        out: list[str] = []
        last_header: Optional[str] = None
        emitted_header: Optional[str] = None

        for raw in log_path.read_text(errors="replace").splitlines():
            clean = strip_ansi(raw).lstrip()
            if header.match(clean):
                last_header = raw
                emitted_header = None
                continue
            if bracket.search(clean) or recap.match(clean):
                if last_header is not None and emitted_header is not last_header:
                    out.append(last_header)
                    emitted_header = last_header
                out.append(raw)
        return out

    def _render_lines(self, lines: list[str]) -> None:
        widget = self.query_one("#host-log-content", RichLog)
        widget.clear()
        if not lines:
            widget.write(Text("(no log entries for this host)", style="dim"))
            return
        for line in lines:
            widget.write(Text.from_ansi(line))
        widget.scroll_home(animate=False)

    def action_focus_search(self) -> None:
        self.query_one("#host-log-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "host-log-search":
            return
        q = event.value.strip().lower()
        if not q:
            self._render_lines(self._all_lines)
            return
        filtered = [l for l in self._all_lines if q in strip_ansi(l).lower()]
        self._render_lines(filtered)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "host-log-search":
            self.query_one("#host-log-content", RichLog).focus()


# ── theme switcher ────────────────────────────────────────────────────────────

class ThemeItem(ListItem):
    def __init__(self, name: str, current: bool) -> None:
        super().__init__()
        self.theme_name = name
        self._current = current

    def compose(self) -> ComposeResult:
        tc = _THEME_COLORS.get(self.theme_name, next(iter(_THEME_COLORS.values())))
        t = _THEMES.get(self.theme_name)
        palette = [
            t.background, t.surface, t.panel, getattr(t, "boost", t.panel),
            t.foreground, t.primary, t.secondary, t.accent,
            tc["ok"], tc["changed"], tc["failed"], tc["unreachable"],
        ] if t else [tc["ok"], tc["changed"], tc["failed"], tc["unreachable"]]
        swatches = "".join(f"[{c}]█[/{c}]" for c in palette if c)
        label = THEME_DESCRIPTIONS.get(self.theme_name, self.theme_name)
        if self._current:
            yield Label(
                f"{swatches}  [bold][{tc['accent']}]{label}[/{tc['accent']}][/bold]"
                f"  [{tc['ok']}]✓[/{tc['ok']}]"
            )
        else:
            yield Label(f"{swatches}  [{tc['accent']}]{label}[/{tc['accent']}]")


class ThemeSwitcherScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, current_theme: str) -> None:
        super().__init__()
        self._current_theme = current_theme

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-modal"):
            yield Label("Select Theme", id="theme-modal-title")
            yield ListView(
                *[ThemeItem(name, name == self._current_theme) for name in _THEMES],
                id="theme-list",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#theme-list", ListView)
        names = list(_THEMES.keys())
        if self._current_theme in names:
            lv.index = names.index(self._current_theme)

    def action_cursor_down(self) -> None:
        self.query_one("#theme-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#theme-list", ListView).action_cursor_up()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if hasattr(item, "theme_name"):
            self.dismiss(item.theme_name)


# ── app ───────────────────────────────────────────────────────────────────────

class AnsibleLogViewerApp(App):
    TITLE = "Ansible Log Viewer"
    ENABLE_COMMAND_PALETTE = False  # suppresses the built-in ctrl+p binding
    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Settings", show=True),
        Binding("t", "change_theme", "Theme"),
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
        Binding("ctrl+right", "next_tab", "Next tab", show=False),
        Binding("ctrl+left", "prev_tab", "Prev tab", show=False),
        Binding("]", "next_tab", "Next tab", show=False),
        Binding("[", "prev_tab", "Prev tab", show=False),
    ]

    DEFAULT_CSS = """
    Header {
        background: $panel;
        color: $primary;
        text-style: bold;
    }

    Footer {
        background: $panel;
        color: $primary;
    }

    Footer > .footer--key {
        background: $primary 20%;
        color: $primary;
        text-style: bold;
    }

    Footer > .footer--description {
        color: $accent;
    }

    Footer > .footer--spacer {
        background: $panel;
    }

    #main {
        layout: horizontal;
        height: 1fr;
    }

    RunListPanel {
        width: 30%;
        min-width: 26;
        max-width: 40;
        border-right: tall $primary;
        height: 1fr;
        overflow: hidden;
        background: $surface;
    }

    #runs-label {
        width: 100%;
        text-align: center;
        background: $panel;
        color: $accent;
        padding: 0 1;
        text-style: bold;
    }

    RunListView {
        height: 1fr;
        background: $surface;
    }

    RunListView > ListItem {
        padding: 0 1;
        background: $surface;
        color: $text;
    }

    RunListView > ListItem:hover {
        background: $panel;
    }

    RunListView > ListItem.--highlight {
        background: $boost;
        color: $text;
    }

    ContentPanel {
        width: 1fr;
        height: 1fr;
        background: $background;
    }

    TabbedContent {
        height: 1fr;
        background: $background;
    }

    TabbedContent ContentSwitcher {
        height: 1fr;
        background: $background;
    }

    Tabs {
        background: $surface;
        border-bottom: solid $primary;
    }

    Tab {
        color: $text-muted;
    }

    Tab:hover {
        color: $text;
    }

    Tab.-active .tab--label {
        color: $accent;
        text-style: bold;
    }

    SummaryTab {
        padding: 1 2;
        height: 1fr;
        color: $text;
        background: $background;
    }

    HostsTab {
        padding: 1 2;
        height: 1fr;
        layout: vertical;
        background: $background;
    }

    #hosts-filter-label {
        color: $accent;
        padding: 0 0 1 0;
        height: 2;
    }

    #hosts-table {
        height: 1fr;
    }

    DataTable {
        background: $background;
        color: $text;
    }

    DataTable > .datatable--header {
        background: $panel;
        color: $primary;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: $boost;
        color: $text;
    }

    DataTable > .datatable--even-row {
        background: $background;
    }

    DataTable > .datatable--odd-row {
        background: $surface;
    }

    TasksTab {
        padding: 1 2;
        height: 1fr;
        layout: vertical;
        background: $background;
    }

    #tasks-table {
        height: 1fr;
    }

    FactsTab {
        padding: 1 2;
        height: 1fr;
        background: $background;
        color: $text;
    }

    #facts-content-inner {
        width: 100%;
    }

    RawLogTab {
        height: 1fr;
        background: $background;
    }

    #raw-log {
        height: 1fr;
        padding: 0 1;
        background: $background;
    }

    WarningsTab {
        height: 1fr;
        padding: 1 2;
        background: $background;
        color: $text;
    }

    #warnings-content {
        width: 100%;
    }

    ThemeSwitcherScreen {
        align: center middle;
        background: $background 60%;
    }

    #theme-modal {
        width: 72;
        height: auto;
        max-height: 40;
        background: $panel;
        border: tall $primary;
        padding: 1 2;
    }

    #theme-modal-title {
        width: 100%;
        text-align: center;
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }

    #theme-list {
        background: $panel;
        height: auto;
        max-height: 30;
        border: none;
    }

    #theme-list > ListItem {
        background: $panel;
        padding: 1 2;
    }

    #theme-list > ListItem:hover {
        background: $surface;
    }

    #theme-list > ListItem.--highlight {
        background: $surface;
    }

    HostLogModal {
        align: center middle;
        background: $background 60%;
    }

    #host-log-modal {
        width: 90%;
        height: 90%;
        background: $panel;
        border: tall $primary;
        padding: 1 2;
        layout: vertical;
    }

    #host-log-title {
        width: 100%;
        height: 1;
        padding: 0 0 1 0;
    }

    #host-log-search {
        height: 3;
        margin-bottom: 1;
        border: tall $primary 40%;
        background: $surface;
    }

    #host-log-search:focus {
        border: tall $accent;
    }

    #host-log-content {
        height: 1fr;
        background: $background;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[RunData] = []
        self._selected_run: Optional[RunData] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield RunListPanel()
            yield ContentPanel()
        yield Footer()

    def on_mount(self) -> None:
        for t in _THEMES.values():
            self.register_theme(t)
        self.theme = _config.theme if _config.theme in _THEMES else "catppuccin-mocha"
        self.title = f"Ansible Log Viewer ({self.theme})"
        self._load_runs()

    @work(thread=True)
    def _load_runs(self) -> None:
        runs = parse_all_runs()
        self.call_from_thread(self._on_runs_loaded, runs)

    def _on_runs_loaded(self, runs: list[RunData]) -> None:
        self._runs = runs
        self.query_one(RunListPanel).populate(runs)
        lv = self.query_one("#run-list", RunListView)
        if len(lv.children) > 1:
            def _select_newest() -> None:
                lv.index = 1
                lv.focus()
            self.call_after_refresh(_select_newest)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "run-list":
            return
        item = event.item
        run = getattr(item, "run", None)
        self._selected_run = run
        self.query_one(ContentPanel).show(run, self._runs)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-raw" and self._selected_run is not None:
            self.query_one("#raw-content", RawLogTab).load(self._selected_run)

    def action_change_theme(self) -> None:
        def _on_result(name: str | None) -> None:
            if name:
                self._apply_theme(name)
        self.push_screen(ThemeSwitcherScreen(_config.theme), _on_result)

    def _apply_theme(self, name: str) -> None:
        if name not in _THEMES:
            return
        new_colors = _THEME_COLORS[name]
        _colors.update(new_colors)
        STATUS_COLOR.update({
            "success": new_colors["ok"], "partial": new_colors["changed"],
            "failed": new_colors["failed"], "empty": new_colors["dim"],
        })
        HOST_STATUS_COLOR.update({
            "ok": new_colors["ok"], "changed": new_colors["changed"],
            "failed": new_colors["failed"], "unreachable": new_colors["unreachable"],
        })
        self.theme = name
        self.title = f"Ansible Log Viewer ({name})"
        _config.theme = name
        save_config(_config)
        # repopulate UI to pick up new colors in Rich markup
        lv = self.query_one("#run-list", RunListView)
        current_idx = lv.index
        self.query_one(RunListPanel).populate(self._runs)
        if current_idx is not None:
            lv.index = current_idx
        self.query_one(ContentPanel).show(self._selected_run, self._runs)

    def action_command_palette(self) -> None:
        if not CommandPalette.is_open(self):
            self.push_screen(CommandPalette())

    def action_reload(self) -> None:
        global _FACT_CACHE
        _FACT_CACHE = None
        self._load_runs()

    def action_next_tab(self) -> None:
        self.query_one("#tabs Tabs", Tabs).action_next_tab()

    def action_prev_tab(self) -> None:
        self.query_one("#tabs Tabs", Tabs).action_previous_tab()


if __name__ == "__main__":
    app = AnsibleLogViewerApp()
    app.run()
