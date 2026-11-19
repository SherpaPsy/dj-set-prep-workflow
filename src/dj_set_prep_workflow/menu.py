from __future__ import annotations

from typing import Callable

from .apple_music_import import run_apple_music_import
from .detect_transcodes import run_transcode_scan
from .prepare_source_files import run_prepare_source_files
from .run_set_prep_flow import build_parser, build_prep_paths, list_source_files, run_flow

def _print_menu(prep_root: str) -> None:
    print("\nDJ Set Prep")
    print(f"Prep root: {prep_root}\n")
    for i, (label, description, _action) in enumerate(MENU_ITEMS, start=1):
        print(f"  {i}. {label} - {description}")


def _prompt_choice() -> int:
    while True:
        raw = input(f"\nSelect 1-{len(MENU_ITEMS)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(MENU_ITEMS):
            return int(raw)
        print("Please enter a number from the menu.")


def _run(clean_start: bool, dry_run: bool) -> None:
    args = build_parser().parse_args([])
    run_flow(
        prep_root=args.prep_root,
        source_dir=args.source_dir,
        ffmpeg_exe=args.ffmpeg_exe,
        reaper_exe=args.reaper_exe,
        reaper_project=args.reaper_project,
        default_genre=args.default_genre,
        max_tracks=args.max_tracks,
        clean_start=clean_start,
        dry_run=dry_run,
        confirm_steps=args.confirm_steps,
        skip_transcode_check=args.skip_transcode_check,
    )


def _transcode_check_only() -> None:
    args = build_parser().parse_args([])
    paths = build_prep_paths(args.prep_root)
    source_dir = args.source_dir or paths.source_files
    source_files = list_source_files(source_dir)

    if not source_files:
        print(f"No supported source audio files found in: {source_dir}")
        return

    print(f"Source files dir: {source_dir}")
    print(f"Source files discovered: {len(source_files)}")
    run_transcode_scan(source_files)


def _prepare_source_files() -> None:
    run_prepare_source_files(prep_root=build_parser().parse_args([]).prep_root)


def _import_to_apple_music() -> None:
    run_apple_music_import(prep_root=build_parser().parse_args([]).prep_root)


# Ordered to match the weekly flow: 1 -> 2 -> 3. Each entry is
# (label, description, action); action None means quit.
MENU_ITEMS: list[tuple[str, str, Callable[[], None] | None]] = [
    ("Prepare source files", "Clear SourceFiles, copy/convert from ReleasePromo, apply title, artist and any missing year/album artist/album from tracklist.csv.", _prepare_source_files),
    ("Run clean", "Clear ConvertedFiles, ProcessedFiles, and TaggedFiles, then process all source files.", lambda: _run(clean_start=True, dry_run=False)),
    ("Import to Apple Music", "Import TaggedFiles into a new playlist named for the upcoming Saturday (YYYY.MM.DD).", _import_to_apple_music),
    ("Run", "Process all source files (no cleanup first).", lambda: _run(clean_start=False, dry_run=False)),
    ("Run clean (dry-run)", "Preview a clean run without touching any files.", lambda: _run(clean_start=True, dry_run=True)),
    ("Transcode check only", "Scan SourceFiles for likely transcodes and print the report. No processing.", _transcode_check_only),
    ("Quit", "Exit without doing anything.", None),
]


def main() -> None:
    default_prep_root = build_parser().parse_args([]).prep_root

    while True:
        _print_menu(str(default_prep_root))
        _label, _description, action = MENU_ITEMS[_prompt_choice() - 1]

        if action is None:
            print("Bye.")
            return
        action()

        again = input("\nReturn to menu? [Y/n]: ").strip().lower()
        if again == "n":
            return


if __name__ == "__main__":
    main()
