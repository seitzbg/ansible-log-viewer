"""Tests for the log-viewer parsing layer, run against the sample fixtures in
examples/. Load with:

    uv run --with pytest --with textual --with rich pytest

(view-logs.py is imported by file path because of the hyphen in its name.)
"""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LOGS = _ROOT / "examples" / "logs"

# Import the hyphenated script as a module (conftest has already set ALV_*).
_spec = importlib.util.spec_from_file_location("view_logs", _ROOT / "view-logs.py")
vl = importlib.util.module_from_spec(_spec)
sys.modules["view_logs"] = vl
_spec.loader.exec_module(vl)


def _clean(name: str) -> str:
    return vl.strip_ansi((_LOGS / name).read_text())


# ── PLAY RECAP ────────────────────────────────────────────────────────────────

def test_parse_play_recap_counts_and_status():
    hosts = vl.parse_play_recap(_clean("daily-2026-05-31_06-00-04.log"))
    assert len(hosts) == 9
    by_name = {h.hostname: h for h in hosts}
    assert by_name["web02.example.net"].changed == 3
    assert by_name["web02.example.net"].status == "changed"
    assert by_name["web01.example.net"].status == "ok"


def test_parse_play_recap_handles_failed_and_unreachable():
    hosts = vl.parse_play_recap(_clean("daily-2026-05-30_06-00-05.log"))
    by_name = {h.hostname: h for h in hosts}
    assert by_name["db01.example.net"].failed == 1
    assert by_name["db01.example.net"].status == "failed"
    assert by_name["backup01.example.net"].unreachable == 1
    assert by_name["backup01.example.net"].status == "unreachable"


def test_host_severity_ordering():
    # unreachable sorts before failed before changed before ok
    assert vl.HostStat("h", 1, 0, 1, 0, 0, 0, 0).severity < \
        vl.HostStat("h", 1, 0, 0, 1, 0, 0, 0).severity


@pytest.mark.parametrize("logfile,expected", [
    ("daily-2026-05-31_06-00-04.log", "success"),
    ("daily-2026-05-30_06-00-05.log", "partial"),  # failed + unreachable hosts
    ("daily-2026-05-28_06-00-06.log", "failed"),    # nothing ok/changed
    ("daily-2026-05-27_06-00-04.log", "empty"),     # zero-byte log
])
def test_determine_status(logfile, expected):
    run = vl.parse_run(_LOGS / logfile, None)
    assert run.status == expected


# ── TASKS RECAP ───────────────────────────────────────────────────────────────

def test_parse_tasks_recap_sorted_and_duration():
    dur_str, dur_s, tasks = vl.parse_tasks_recap(_clean("daily-2026-05-31_06-00-04.log"))
    assert dur_str == "0:03:12.451"
    assert dur_s == pytest.approx(3 * 60 + 12.451)
    assert tasks[0].name == "apt : Upgrade all packages"
    assert tasks[0].duration_s == 84.21
    # sorted descending
    assert [t.duration_s for t in tasks] == sorted((t.duration_s for t in tasks), reverse=True)


def test_parse_tasks_recap_absent():
    assert vl.parse_tasks_recap("no recap here") == (None, None, [])


# ── facts.py mirror parsing ─────────────────────────────────────────────────────

def test_parse_facts_mirror_success():
    fm = vl.parse_facts_mirror(_clean("daily-2026-05-31_06-00-04.log"))
    assert fm.present and fm.redis_ok and not fm.command_failed
    assert fm.mirrored == 8
    assert fm.skipped == 0
    assert fm.snapshot_dir and fm.snapshot_dir.endswith("facts-snapshot")
    assert not fm.degraded


def test_parse_facts_mirror_degraded():
    fm = vl.parse_facts_mirror(_clean("daily-2026-05-29_06-00-03.log"))
    assert fm.present and not fm.redis_ok and fm.degraded
    assert any("redis skipped" in w for w in fm.warnings)


def test_parse_facts_mirror_command_failed():
    fm = vl.parse_facts_mirror(_clean("daily-2026-05-28_06-00-06.log"))
    assert fm.command_failed and fm.degraded
    assert "ConnectionError" in (fm.fail_reason or "")


def test_parse_facts_mirror_absent_on_empty_log():
    fm = vl.parse_facts_mirror("")
    assert not fm.present and not fm.degraded


# ── timestamps & fact snapshots ─────────────────────────────────────────────────

def test_parse_timestamp_from_filename():
    ts = vl.parse_timestamp(_LOGS / "daily-2026-05-31_06-00-04.log")
    assert ts == datetime(2026, 5, 31, 6, 0, 4)


def test_facts_for_resolves_short_alias_to_fqdn():
    # snapshots are stored as <host>.example.net.yml; a short recap name resolves
    direct = vl.facts_for("db01.example.net")
    alias = vl.facts_for("db01")
    assert direct.get("ansible_distribution") == "Ubuntu"
    assert direct.get("primary_ipv4") == "192.0.2.21"
    assert alias == direct


def test_parse_all_runs_orders_newest_first():
    runs = vl.parse_all_runs()
    assert [r.status for r in runs] == [
        "success", "partial", "partial", "failed", "empty", "success",
    ]
    assert runs[0].timestamp > runs[-1].timestamp
