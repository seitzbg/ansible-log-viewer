"""Point the viewer at the committed sample data before it is imported.

view-logs.py reads its config (and resolves LOG_DIR / ansible_home) at import
time, so these env vars must be set in conftest — pytest imports conftest.py
before any test module — and we use a throwaway config path so a developer's
real ~/.config file is never read or written during tests.
"""
import os
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

os.environ["ALV_LOG_DIR"] = str(_ROOT / "examples" / "logs")
os.environ["ALV_HOME"] = str(_ROOT / "examples")
os.environ["ALV_CONFIG"] = str(Path(tempfile.gettempdir()) / "alv-pytest-config.toml")
os.environ["ALV_THEME"] = "catppuccin-mocha"
