#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual", "rich"]
# ///
"""Generate showcase screenshots of the Ansible Log Viewer."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# Point the viewer at the committed sample data (not the user's real logs) and
# at a throwaway config, so screenshots are reproducible and never leak a real
# inventory. Must be set before view-logs.py is imported — it reads config at
# import time.
_HERE = Path(__file__).parent
os.environ.setdefault("ALV_LOG_DIR", str(_HERE / "examples" / "logs"))
os.environ.setdefault("ALV_HOME", str(_HERE / "examples"))
os.environ.setdefault("ALV_CONFIG", str(Path(tempfile.gettempdir()) / "alv-screenshots-config.toml"))
os.environ.setdefault("ALV_THEME", "catppuccin-mocha")

spec = importlib.util.spec_from_file_location(
    "view_logs", Path(__file__).parent / "view-logs.py"
)
view_logs = importlib.util.module_from_spec(spec)
sys.modules["view_logs"] = view_logs
spec.loader.exec_module(view_logs)

AnsibleLogViewerApp = view_logs.AnsibleLogViewerApp
HostLogModal = view_logs.HostLogModal
RawLogTab = view_logs.RawLogTab

from textual.widgets import ListView, TabbedContent

OUT = Path(__file__).parent / "screenshots"
OUT.mkdir(exist_ok=True)

# Terminal sizes are kept modest on purpose: a tall/wide capture exports a huge
# SVG that GitHub then shrinks to the README column, making the text unreadable.
# Most screens read well at ~112 cols; the hosts table needs more width for its
# fact columns, so it gets its own size.
SIMPLE = (112, 32)   # summary / tasks / facts / warnings / themes
WIDE = (190, 30)     # hosts table (extra columns: distro / version / kernel …)
TALL = (122, 34)     # raw log / host-log modal (more rows of content)

# Run-list indices: 0 = "All Runs" aggregate, 1 = newest run (05-31, success),
# 2 = 05-30 (partial: a failed + an unreachable host). app._runs is 0-based and
# newest-first, so the run at list index N is app._runs[N - 1].


async def _wait_loaded(pilot, app) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if len(app.query_one("#run-list", ListView).children) > 1:
            return


async def shot(filename, size, *, run_index=1, theme=None, tab="tab-summary", steps=None):
    app = AnsibleLogViewerApp()
    async with app.run_test(size=size) as pilot:
        await _wait_loaded(pilot, app)
        # the app auto-selects the newest run via call_after_refresh; let that
        # fire before we override the selection, or it clobbers our run_index.
        await pilot.pause(0.35)
        if theme:
            app._apply_theme(theme)
            await pilot.pause(0.15)
        app.query_one("#run-list", ListView).index = run_index
        await pilot.pause(0.25)
        app.query_one("#tabs", TabbedContent).active = tab
        await pilot.pause(0.25)
        if tab == "tab-raw":  # the raw log loads lazily on tab activation
            app.query_one("#raw-content", RawLogTab).load(app._selected_run)
            await pilot.pause(0.2)
        if steps is not None:
            await steps(pilot, app)
            await pilot.pause(0.3)
        (OUT / filename).write_text(app.export_screenshot())
        print("✓ ", filename)


async def _open_host_modal(pilot, app):
    # drill into the failed db01 host on the 05-30 partial run
    app.push_screen(HostLogModal("db01.example.net", app._runs[1]))
    await pilot.pause(0.4)


async def _open_theme_switcher(pilot, app):
    await pilot.press("t")
    await pilot.pause(0.3)


async def take_screenshots() -> None:
    # tabs in order: Summary · Hosts · Tasks · Facts · Raw Log · Warnings
    await shot("01-summary.svg", SIMPLE, run_index=1, tab="tab-summary")
    await shot("02-hosts.svg", WIDE, run_index=2, tab="tab-hosts")
    await shot("03-host-log-modal.svg", TALL, run_index=2, tab="tab-hosts", steps=_open_host_modal)
    await shot("04-tasks.svg", SIMPLE, run_index=1, tab="tab-tasks")
    await shot("05-facts.svg", SIMPLE, run_index=1, tab="tab-facts")
    await shot("06-raw-log.svg", TALL, run_index=2, tab="tab-raw")
    await shot("07-warnings.svg", SIMPLE, run_index=2, tab="tab-warnings")
    await shot("08-all-runs.svg", SIMPLE, run_index=0, tab="tab-summary")
    await shot("09-theme-switcher.svg", SIMPLE, run_index=1, steps=_open_theme_switcher)
    await shot("10-theme-tokyonight.svg", SIMPLE, run_index=1, theme="tokyonight-night")
    await shot("11-theme-cyberpunk.svg", SIMPLE, run_index=1, theme="cyberpunk-2077")
    await shot("12-theme-oxocarbon.svg", WIDE, run_index=2, theme="oxocarbon-dark", tab="tab-hosts")
    print(f"\nAll screenshots saved to {OUT}/")


asyncio.run(take_screenshots())
