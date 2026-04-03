from __future__ import annotations

import sys
from pathlib import Path


def prep_root_candidates() -> list[Path]:
    home = Path.home()

    if sys.platform == "win32":
        return [
            home / "Music" / "DJ-Set-Prep",
            home / "OneDrive" / "Music" / "DJ-Set-Prep",
        ]

    if sys.platform == "darwin":
        return [
            home / "Music" / "DJ-Set-Prep",
            home / "Library" / "CloudStorage" / "OneDrive-Personal" / "Music" / "DJ-Set-Prep",
            home / "OneDrive" / "Music" / "DJ-Set-Prep",
        ]

    return [
        home / "Music" / "DJ-Set-Prep",
        home / "OneDrive" / "Music" / "DJ-Set-Prep",
    ]


def resolve_default_prep_root() -> Path:
    candidates = prep_root_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]