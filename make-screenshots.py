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
_THEMES = view_logs._THEMES

from textual.widgets import ListView, TabbedContent

OUT = Path(__file__).parent / "screenshots"
OUT.mkdir(exist_ok=True)


async def take_screenshots() -> None:
    async with AnsibleLogViewerApp().run_test(size=(232, 48)) as pilot:
        app = pilot.app

        # Wait for runs to load and the list to populate
        await pilot.pause(0.5)
        await pilot.pause(0.5)

        lv = app.query_one("#run-list", ListView)

        # ── 1. Summary – newest run ───────────────────────────────────────────
        # The newest run is already selected (index 1) after load
        await pilot.pause(0.3)
        svg = app.export_screenshot()
        (OUT / "01-summary-run.svg").write_text(svg)
        print("✓  01-summary-run.svg")

        # ── 2. Hosts tab ──────────────────────────────────────────────────────
        await pilot.press("]")           # next tab → Hosts
        await pilot.pause(0.2)
        svg = app.export_screenshot()
        (OUT / "02-hosts-tab.svg").write_text(svg)
        print("✓  02-hosts-tab.svg")

        # ── 3. Tasks tab ──────────────────────────────────────────────────────
        await pilot.press("]")           # next tab → Tasks
        await pilot.pause(0.2)
        svg = app.export_screenshot()
        (OUT / "03-tasks-tab.svg").write_text(svg)
        print("✓  03-tasks-tab.svg")

        # ── 4. Raw Log tab ────────────────────────────────────────────────────
        await pilot.press("]")           # next tab → Raw Log
        await pilot.pause(0.5)
        svg = app.export_screenshot()
        (OUT / "04-raw-log-tab.svg").write_text(svg)
        print("✓  04-raw-log-tab.svg")

        # ── 5. Warnings tab ───────────────────────────────────────────────────
        await pilot.press("]")           # next tab → Warnings
        await pilot.pause(0.2)
        svg = app.export_screenshot()
        (OUT / "05-warnings-tab.svg").write_text(svg)
        print("✓  05-warnings-tab.svg")

        # ── 6. All-Runs aggregate Summary ─────────────────────────────────────
        await pilot.press("[")           # back to Summary tab
        await pilot.press("[")
        await pilot.press("[")
        await pilot.press("[")
        await pilot.pause(0.1)
        lv.index = 0                     # select "All Runs"
        await pilot.pause(0.3)
        svg = app.export_screenshot()
        (OUT / "06-summary-aggregate.svg").write_text(svg)
        print("✓  06-summary-aggregate.svg")

        # ── 7. Theme switcher modal ───────────────────────────────────────────
        await pilot.press("t")
        await pilot.pause(0.3)
        svg = app.export_screenshot()
        (OUT / "07-theme-switcher.svg").write_text(svg)
        print("✓  07-theme-switcher.svg")
        await pilot.press("escape")
        await pilot.pause(0.1)

        # ── 8. Tokyo Night theme ──────────────────────────────────────────────
        app.call_from_thread if False else None
        app._apply_theme("tokyonight-night")
        await pilot.pause(0.3)
        lv.index = 1
        await pilot.pause(0.3)
        svg = app.export_screenshot()
        (OUT / "08-tokyonight-summary.svg").write_text(svg)
        print("✓  08-tokyonight-summary.svg")

        # ── 9. Cyberpunk theme ────────────────────────────────────────────────
        app._apply_theme("cyberpunk-2077")
        await pilot.pause(0.3)
        svg = app.export_screenshot()
        (OUT / "09-cyberpunk-summary.svg").write_text(svg)
        print("✓  09-cyberpunk-summary.svg")

        # ── 10. Oxocarbon hosts tab ───────────────────────────────────────────
        app._apply_theme("oxocarbon-dark")
        await pilot.pause(0.2)
        await pilot.press("]")           # Hosts tab
        await pilot.pause(0.2)
        svg = app.export_screenshot()
        (OUT / "10-oxocarbon-hosts.svg").write_text(svg)
        print("✓  10-oxocarbon-hosts.svg")

        print(f"\nAll screenshots saved to {OUT}/")


asyncio.run(take_screenshots())
