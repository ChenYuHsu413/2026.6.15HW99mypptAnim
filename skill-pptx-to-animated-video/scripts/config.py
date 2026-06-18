"""Load project/voice/caption settings for the pipeline scripts.

Defaults live in <skill>/config/*.json (the single source of truth, equal to
the values that used to be hardcoded). A task may drop its own
project_config.json / voice_config.json / caption_config.json in its project
root (cwd) to override any subset -- those are deep-merged over the defaults.
This is the layer the UI writes to per task.
"""

import json
from pathlib import Path

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "config"


def _merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _load(name):
    defaults = json.loads((DEFAULTS_DIR / name).read_text(encoding="utf-8"))
    local = Path.cwd() / name
    if local.exists():
        return _merge(defaults, json.loads(local.read_text(encoding="utf-8")))
    return defaults


PROJECT = _load("project_config.json")
VOICE = _load("voice_config.json")
CAPTION = _load("caption_config.json")
