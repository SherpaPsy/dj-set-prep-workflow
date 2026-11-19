"""Import TaggedFiles into Apple Music as a new playlist named for the upcoming Saturday.

The workflow is atomic: one batch of tracks -> one new playlist. So:
- the playlist (e.g. "2026.11.14") must NOT already exist;
- none of the tracks should already be in the library;
- the Music media folder (an external drive) must be mounted, and after import
  every track must live inside it (i.e. Music copied the file rather than
  linking to TaggedFiles, which gets cleared on the next run).

Talks to Music.app via AppleScript (osascript). The first run triggers a macOS
prompt to allow your terminal to control Music.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from .paths import resolve_default_prep_root
from .prepare_source_files import read_tags

IMPORT_EXTENSIONS = {".aif", ".aiff", ".mp3", ".m4a", ".wav"}
SATURDAY = 5  # date.weekday(): Monday=0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def upcoming_saturday(today: date | None = None) -> date:
    """Today if it's Saturday, otherwise the next Saturday."""
    today = today or date.today()
    return today + timedelta(days=(SATURDAY - today.weekday()) % 7)


def playlist_name_for(day: date) -> str:
    return day.strftime("%Y.%m.%d")


def _osascript(script: str, *args: str) -> str:
    """Run an AppleScript with `on run argv`, passing args safely (no string splicing)."""
    result = subprocess.run(
        ["osascript", "-", *args],
        input=script,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.strip()


def music_media_folder() -> Path | None:
    """The Music media folder from Music's preferences, or None if unknown."""
    result = subprocess.run(
        ["defaults", "read", "com.apple.Music", "media-folder-url"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(unquote(urlparse(result.stdout.strip()).path))


def playlist_exists(name: str) -> bool:
    script = """
on run argv
    set plName to item 1 of argv
    tell application "Music"
        return (exists (some user playlist whose name is plName)) as string
    end tell
end run
"""
    return _osascript(script, name) == "true"


def find_library_tracks(title: str, artist: str) -> list[str]:
    """Persistent IDs of library tracks with this exact title + artist (case-insensitive)."""
    # Fetch the artist's tracks, then compare titles loosely in Python so
    # spacing/case/punctuation differences (e.g. a double space) still match.
    script = """
on run argv
    set wantArtist to item 1 of argv
    set rows to {}
    tell application "Music"
        set found to (every track of library playlist 1 whose artist is wantArtist)
        repeat with t in found
            set end of rows to (persistent ID of t) & tab & (name of t)
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return rows as text
end run
"""
    out = _osascript(script, artist)
    want = _loose(title)
    ids = []
    for line in out.splitlines():
        pid, _, name = line.partition("\t")
        if pid.strip() and _loose(name) == want:
            ids.append(pid.strip())
    return ids


def _loose(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def create_playlist(name: str) -> None:
    script = """
on run argv
    set plName to item 1 of argv
    tell application "Music"
        make new user playlist with properties {name:plName}
    end tell
end run
"""
    _osascript(script, name)


def import_file(file_path: Path) -> str:
    """Add a file to the library. Returns its persistent ID, or "" if Music
    didn't hand back a track reference (it sometimes doesn't, even on success)."""
    script = """
on run argv
    set f to POSIX file (item 1 of argv)
    tell application "Music"
        with timeout of 600 seconds
            try
                set t to add f
                return persistent ID of t
            on error
                return ""
            end try
        end timeout
    end tell
end run
"""
    return _osascript(script, str(file_path))


def playlist_track_ids(playlist: str) -> set[str]:
    script = """
on run argv
    set plName to item 1 of argv
    tell application "Music"
        set pl to user playlist plName
        -- Asking an empty playlist for "every track" is an error (-1728).
        if (count of tracks of pl) is 0 then return ""
        set ids to persistent ID of every track of pl
    end tell
    set AppleScript's text item delimiters to linefeed
    return ids as text
end run
"""
    return {line.strip() for line in _osascript(script, playlist).splitlines() if line.strip()}


def track_location(persistent_id: str) -> str:
    """POSIX path of a library track's file, or "" if Music doesn't know it (yet)."""
    script = """
on run argv
    set pid to item 1 of argv
    tell application "Music"
        try
            set loc to location of (first track of library playlist 1 whose persistent ID is pid)
        on error
            return ""
        end try
    end tell
    if loc is missing value then return ""
    return POSIX path of loc
end run
"""
    return _osascript(script, persistent_id)


def _find_after_import(title: str, artist: str, attempts: int = 10) -> str:
    """Music can take a moment to index a freshly added file; poll for it."""
    for _ in range(attempts):
        ids = find_library_tracks(title, artist)
        if ids:
            return ids[0]
        time.sleep(1)
    return ""


def add_existing_track_to_playlist(persistent_id: str, playlist: str) -> None:
    script = """
on run argv
    set pid to item 1 of argv
    set plName to item 2 of argv
    tell application "Music"
        set t to first track of library playlist 1 whose persistent ID is pid
        duplicate t to user playlist plName
    end tell
end run
"""
    _osascript(script, persistent_id, playlist)


def _is_within(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _banner(lines: list[str]) -> None:
    bar = "!" * 72
    print(f"\n{bar}")
    for line in lines:
        print(f"!!  {line}")
    print(bar)


# --------------------------------------------------------------------------- #
# Flow
# --------------------------------------------------------------------------- #


def run_apple_music_import(
    prep_root: Path,
    tagged_dir: Path | None = None,
    playlist_name: str | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> bool:
    if sys.platform != "darwin":
        print("[ERROR] Apple Music import only works on macOS.")
        return False

    tagged_dir = tagged_dir or prep_root / "TaggedFiles"
    playlist_name = playlist_name or playlist_name_for(upcoming_saturday())

    files = sorted(
        p for p in tagged_dir.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in IMPORT_EXTENSIONS
    ) if tagged_dir.is_dir() else []
    if not files:
        print(f"[ERROR] No audio files found in {tagged_dir}")
        return False

    print(f"Tagged files:  {tagged_dir}  ({len(files)} file(s))")
    print(f"Playlist:      {playlist_name}")

    # --- Check 1: media folder (external drive) is reachable --------------
    media_folder = music_media_folder()
    if media_folder is None:
        print("[WARN] Could not read Music's media folder from preferences; skipping copy verification.")
    elif not media_folder.exists():
        _banner([
            "Music media folder is NOT reachable:",
            f"  {media_folder}",
            "Is the external drive connected and mounted?",
        ])
        return False
    else:
        print(f"Media folder:  {media_folder}")

    # --- Check 2: playlist must not exist (or we're resuming) -------------
    try:
        exists = playlist_exists(playlist_name)
    except RuntimeError as exc:
        print(f"[ERROR] Could not talk to Music: {exc}")
        print("        If macOS asked to allow control of Music, click Allow and retry.")
        print("        (System Settings > Privacy & Security > Automation)")
        return False

    resume = False
    in_playlist: set[str] = set()
    if exists:
        in_playlist = playlist_track_ids(playlist_name)
        _banner([
            f"Playlist '{playlist_name}' ALREADY EXISTS in Apple Music ({len(in_playlist)} track(s)).",
            "Either this batch was already imported, or a previous import was",
            "interrupted. Resuming adds only what's missing: tracks already in the",
            "playlist are skipped, tracks already in the library are not re-imported.",
        ])
        if dry_run:
            print("[DRY-RUN] Would require typing 'resume' here.")
        elif assume_yes or input("\nType 'resume' to finish this playlist, anything else to abort: ").strip().lower() != "resume":
            print("Aborted. Nothing changed.")
            return False
        resume = True

    # --- Check 3: tracks must not already be in the library ---------------
    already: dict[Path, str] = {}
    tags_by_file = {}
    for path in files:
        tags = read_tags(path)
        tags_by_file[path] = tags
        if not tags.title or not tags.artist:
            print(f"[WARN] Missing title/artist tag: {path.name}")
            continue
        ids = find_library_tracks(tags.title, tags.artist)
        if ids:
            # Prefer the copy that's already in this playlist, if any.
            already[path] = next((i for i in ids if i in in_playlist), ids[0])

    n_done = sum(1 for pid in already.values() if pid in in_playlist)
    n_lib_only = len(already) - n_done
    n_new = len(files) - len(already)

    if resume:
        print(f"Resume plan:   {n_done} already in playlist, {n_lib_only} in library -> add to playlist, {n_new} to import.")
    elif already:
        _banner([
            f"{len(already)} of {len(files)} track(s) are ALREADY in your Music library:",
            *[f"  {tags_by_file[p].artist} - {tags_by_file[p].title}" for p in already],
            "If you continue they will NOT be re-imported; the existing library",
            "tracks will be added to the new playlist instead.",
        ])
        if dry_run:
            print("[DRY-RUN] Would require typing 'continue' here.")
        elif assume_yes or input("\nType 'continue' to proceed anyway, anything else to abort: ").strip().lower() != "continue":
            print("Aborted. Nothing imported.")
            return False
    else:
        print(f"Library check: none of the {len(files)} tracks are in the library yet.")

    if dry_run:
        action = "finish" if exists else "create"
        print(f"\n[DRY-RUN] All checks passed. Would {action} '{playlist_name}' and import {n_new} file(s).")
        return True

    if not resume and not assume_yes:
        if input(f"\nCreate playlist '{playlist_name}' and import {len(files)} track(s)? [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return False

    # --- Import -------------------------------------------------------------
    if not exists:
        print(f"\n[STEP] Create playlist '{playlist_name}'")
        create_playlist(playlist_name)

    print("[STEP] Import tracks")
    not_copied: list[tuple[Path, str]] = []
    failed: list[tuple[Path, str]] = []
    n_location_checked = 0
    for path in files:
        tags = tags_by_file[path]
        label = f"{tags.artist} - {tags.title}" if tags.title else path.name
        try:
            pid = already.get(path, "")
            if pid and pid in in_playlist:
                print(f"  [SKIP]     {label}  (already in playlist)")
            else:
                if pid:
                    status = "[EXISTING]"
                else:
                    # Import to the library only, then add to the playlist by
                    # ID; never trust the reference `add` returns.
                    pid = import_file(path)
                    if not pid:
                        print(f"  [NOTE]     Music returned no track for {path.name}; looking it up by artist + title")
                        pid = _find_after_import(tags.title, tags.artist)
                    if not pid:
                        failed.append((path, "Music didn't report the track after import"))
                        print(f"  [FAILED]   {label}")
                        continue
                    status = "[IMPORT]  "
                if pid not in playlist_track_ids(playlist_name):
                    add_existing_track_to_playlist(pid, playlist_name)
                print(f"  {status} {label}")

            if media_folder is not None:
                location = track_location(pid)
                n_location_checked += 1
                if not location or not _is_within(Path(location), media_folder):
                    not_copied.append((path, location))
        except RuntimeError as exc:
            failed.append((path, str(exc)))
            print(f"  [FAILED]   {label}")

    if not_copied:
        _banner([
            f"{len(not_copied)} track(s) are NOT inside the Music media folder:",
            *[f"  {p.name} -> {loc or '(no location)'}" for p, loc in not_copied],
            "Music linked to the original files instead of copying them.",
            "Turn on: Music > Settings > Files > 'Copy files to Music Media folder",
            "when adding to Library'. Don't clear TaggedFiles until this is fixed.",
        ])
    elif media_folder is not None and n_location_checked:
        scope = "every track's" if n_location_checked == len(files) else f"{n_location_checked} of {len(files)} tracks'"
        print(f"  Verified: {scope} file is inside {media_folder}")

    final_count = len(playlist_track_ids(playlist_name))
    if failed:
        _banner([
            f"{len(failed)} track(s) FAILED:",
            *[f"  {p.name}: {why}" for p, why in failed],
            f"Playlist '{playlist_name}' has {final_count} of {len(files)} tracks.",
            "Re-run 'Import to Apple Music' and type 'resume' to retry the rest.",
        ])
        return False

    print(f"\n[DONE] Playlist '{playlist_name}' has {final_count} of {len(files)} track(s).")
    return final_count == len(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import TaggedFiles into Apple Music as a playlist named for the upcoming Saturday.")
    parser.add_argument("--prep-root", type=Path, default=resolve_default_prep_root())
    parser.add_argument("--tagged-dir", type=Path, default=None, help="Default: TaggedFiles under prep root.")
    parser.add_argument("--playlist", default=None, help="Override playlist name (default: upcoming Saturday, YYYY.MM.DD).")
    parser.add_argument("--dry-run", action="store_true", help="Run all checks, import nothing.")
    parser.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation (still aborts if the playlist or tracks already exist).")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    ok = run_apple_music_import(
        prep_root=args.prep_root,
        tagged_dir=args.tagged_dir,
        playlist_name=args.playlist,
        dry_run=args.dry_run,
        assume_yes=args.yes,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
