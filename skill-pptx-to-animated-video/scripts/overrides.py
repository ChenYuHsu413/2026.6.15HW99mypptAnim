"""Overrides layer -- user/AI edits kept separate from generated artifacts.

overrides.json (project root) stores ONLY the differences a user makes, keyed
by slide ("slide_02") then by what they touched. Generated metadata.json and
narration_script.md stay pristine; build_composition.py applies these edits
when producing the resolved composition.json.

Schema:
  {
    "slide_02": {
      "narration": "edited narration text",
      "notes": "free-form note for the agent",
      "layers": {
        "slide_02_table_01.png": { "start": 1.2, "duration": 0.9,
                                    "animation": "zoom-in", "z": 6 }
      }
    }
  }
"""

import json
from pathlib import Path


def stable_id(slide, name):
    """slide_02_table_01.png -> s02-table-01 (stable while the filename is)."""
    parts = name.rsplit(".", 1)[0].split("_")
    return f"s{int(slide):02d}-" + "-".join(parts[2:])


def slide_key(n):
    return f"slide_{int(n):02d}"


def load(root=None):
    path = (root or Path.cwd()) / "overrides.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def layer(overrides, slide_n, name):
    """Return the override dict for one layer (empty if none)."""
    sl = overrides.get(slide_key(slide_n)) or {}
    return (sl.get("layers") or {}).get(name, {})


def narration(overrides, slide_n):
    """Return overridden narration text for a slide, or None if unedited."""
    sl = overrides.get(slide_key(slide_n)) or {}
    return sl.get("narration")
