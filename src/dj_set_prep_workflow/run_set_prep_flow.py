from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mutagen.aiff import AIFF
from mutagen.id3 import COMM, ID3

from .tag_set_mp3s import (
    INIT_TARGET_PATH,
    MP3_SOURCE,
    YEAR,
    TrackEntry,
    find_mp3_files,
    find_set_file,
    parse_set_file,
    select_match_mp3,
    tag_mp3,
)

DEFAULT_ESSENTIA_EXE = Path(
    r"D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
)
DEFAULT_RX10_EXE = Path(
    r"C:\Program Files\iZotope\RX 10 Audio Editor\win64\RX10Headless.exe"
)
DEFAULT_RX_PRESET = "DJ Set Prep"


@dataclass(slots=True)
class SelectedSet:
    target_path: Path
    set_file: Path


def parse_date_from_folder_name(name: str) -> date | None:
    try:
        return datetime.strptime(name[:10], "%Y.%m.%d").date()
    except Exception:
        return None


def list_set_folders(init_target_path: Path) -> list[Path]:
    if not init_target_path.exists():
        raise FileNotFoundError(f"INIT_TARGET_PATH does not exist: {init_target_path}")
    return sorted([path for path in init_target_path.iterdir() if path.is_dir()])


def suggest_folder_index(folders: list[Path]) -> int:
    today = date.today()
    dated: list[tuple[int, date, Path]] = []

    for idx, folder in enumerate(folders):
        parsed = parse_date_from_folder_name(folder.name)
        if parsed:
            dated.append((idx, parsed, folder))

    future = [item for item in dated if item[1] >= today]
    if future:
        future.sort(key=lambda item: item[1])
        return future[0][0]

    if dated:
        dated.sort(key=lambda item: abs((item[1] - today).days))
        return dated[0][0]

    return 0


def select_target_folder(init_target_path: Path, explicit_target_path: Path | None) -> SelectedSet:
    if explicit_target_path:
        target = explicit_target_path
        set_file = find_set_file(target, None)
        return SelectedSet(target_path=target, set_file=set_file)

    folders = list_set_folders(init_target_path)
    if not folders:
        raise FileNotFoundError(f"No folders found in {init_target_path}")

    suggestion_idx = suggest_folder_index(folders)

    print(f"\nYEAR: {YEAR}")
    print(f"INIT_TARGET_PATH: {init_target_path}")
    print("Available set folders:")
    for idx, folder in enumerate(folders, start=1):
        marker = "  <== suggested" if idx - 1 == suggestion_idx else ""
        print(f"  {idx:>2}. {folder.name}{marker}")

    raw = input(f"Select folder [default {suggestion_idx + 1}]: ").strip()
    if not raw:
        chosen_idx = suggestion_idx
    else:
        chosen_idx = max(0, min(len(folders) - 1, int(raw) - 1))

    target = folders[chosen_idx]
    set_file = find_set_file(target, None)
    return SelectedSet(target_path=target, set_file=set_file)


def match_entries_to_mp3s(
    entries: list[TrackEntry],
    source_dir: Path,
    interactive_unsure: bool,
) -> list[tuple[TrackEntry, Path]]:
    mp3_files = find_mp3_files(source_dir)
    if not mp3_files:
        raise FileNotFoundError(f"No .mp3 files found in source dir: {source_dir}")

    used: set[Path] = set()
    matched: list[tuple[TrackEntry, Path]] = []

    for entry in entries:
        path = select_match_mp3(entry, mp3_files, used, interactive_unsure=interactive_unsure)
        if not path:
            continue
        used.add(path)
        matched.append((entry, path))

    return matched


def run_tagging(matched: list[tuple[TrackEntry, Path]], default_genre: str, dry_run: bool) -> None:
    for entry, mp3_path in matched:
        tag_mp3(mp3_path=mp3_path, entry=entry, default_genre=default_genre, dry_run=dry_run)


def ensure_aiff_dir(target_path: Path) -> Path:
    aiff_dir = target_path / "AIFF"
    aiff_dir.mkdir(parents=True, exist_ok=True)
    return aiff_dir


def ensure_processed_aiff_dir(target_path: Path) -> Path:
    processed_dir = target_path / "aiffProcessed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return processed_dir


def convert_mp3_to_aiff_24bit(matched: list[tuple[TrackEntry, Path]], aiff_dir: Path, ffmpeg_exe: str, dry_run: bool) -> list[Path]:
    outputs: list[Path] = []
    for _, mp3_path in matched:
        output_path = aiff_dir / f"{mp3_path.stem}.aiff"
        cmd = [ffmpeg_exe, "-y", "-i", str(mp3_path), "-c:a", "pcm_s24be", str(output_path)]
        if dry_run:
            print(f"[DRY-RUN] ffmpeg: {' '.join(cmd)}")
        else:
            subprocess.run(cmd, check=True)
        outputs.append(output_path)
    return outputs


def run_rx10_headless(
    aiff_files: list[Path],
    output_dir: Path,
    rx10_exe: Path,
    preset: str,
    dry_run: bool,
) -> list[Path]:
    processed_files: list[Path] = []
    for path in aiff_files:
        output_path = output_dir / path.name
        cmd = [
            str(rx10_exe),
            "--headless",
            "--preset",
            preset,
            "--input",
            str(path),
            "--output",
            str(output_path),
        ]
        if dry_run:
            print(f"[DRY-RUN] RX10: {' '.join(cmd)}")
        else:
            subprocess.run(cmd, check=True)
        processed_files.append(output_path)

    return processed_files


def passthrough_to_processed(aiff_files: list[Path], output_dir: Path, dry_run: bool) -> list[Path]:
    processed_files: list[Path] = []
    for path in aiff_files:
        output_path = output_dir / path.name
        if dry_run:
            print(f"[DRY-RUN] Skip RX10 passthrough: {path} -> {output_path}")
        else:
            shutil.copy2(path, output_path)
        processed_files.append(output_path)
    return processed_files


def run_essentia(aiff_files: list[Path], essentia_exe: Path, dry_run: bool) -> list[Path]:
    json_files: list[Path] = []
    for path in aiff_files:
        json_path = path.with_suffix(".json")
        cmd = [str(essentia_exe), str(path), str(json_path)]
        if dry_run:
            print(f"[DRY-RUN] Essentia: {' '.join(cmd)}")
        else:
            subprocess.run(cmd, check=True)
        json_files.append(json_path)
    return json_files


def extract_essentia_summary(json_path: Path) -> str:
    if not json_path.exists():
        return "essentia:missing"

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    def get_nested(data: dict[str, Any], *keys: str) -> Any:
        current: Any = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    bpm = get_nested(payload, "rhythm", "bpm")
    danceability = get_nested(payload, "rhythm", "danceability")
    key = get_nested(payload, "tonal", "key_key")
    scale = get_nested(payload, "tonal", "key_scale")
    chords_key = get_nested(payload, "tonal", "chords_key")
    chords_scale = get_nested(payload, "tonal", "chords_scale")

    camelot_map = {
        "major": {
            "B": "1B",
            "F#": "2B",
            "Gb": "2B",
            "Db": "3B",
            "C#": "3B",
            "Ab": "4B",
            "G#": "4B",
            "Eb": "5B",
            "D#": "5B",
            "Bb": "6B",
            "A#": "6B",
            "F": "7B",
            "C": "8B",
            "G": "9B",
            "D": "10B",
            "A": "11B",
            "E": "12B",
        },
        "minor": {
            "Ab": "1A",
            "G#": "1A",
            "Eb": "2A",
            "D#": "2A",
            "Bb": "3A",
            "A#": "3A",
            "F": "4A",
            "C": "5A",
            "G": "6A",
            "D": "7A",
            "A": "8A",
            "E": "9A",
            "B": "10A",
            "F#": "11A",
            "Gb": "11A",
            "C#": "12A",
            "Db": "12A",
        },
    }

    def _fmt_float(value: Any, decimals: int = 2) -> str | None:
        try:
            return f"{float(value):.{decimals}f}"
        except Exception:
            return None

    bpm_text = None
    try:
        bpm_text = str(int(round(float(bpm))))
    except Exception:
        bpm_text = None

    energy_text = None
    try:
        energy_text = str(int(round(min(float(danceability), 10))))
    except Exception:
        energy_text = None

    scale_key = str(scale).lower() if scale else None
    key_text = None
    if key and scale_key in camelot_map:
        key_text = camelot_map[scale_key].get(str(key))
    if not key_text and (key or scale):
        key_text = "unknown"

    chords_text = None
    chords_scale_key = str(chords_scale).lower() if chords_scale else None
    if chords_key and chords_scale_key in camelot_map:
        chords_text = camelot_map[chords_scale_key].get(str(chords_key))
    if not chords_text and (chords_key or chords_scale):
        chords_text = "unknown"

    parts = [
        f"bpm={bpm_text}" if bpm_text else None,
        f"key={key_text}" if key_text else None,
        f"chords={chords_text}" if chords_text else None,
        f"energy={energy_text}" if energy_text else None,
    ]
    filtered = [part for part in parts if part]
    return "essentia:" + (";".join(filtered) if filtered else "no-summary")


def add_comment_to_aiff(aiff_path: Path, comment_text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[DRY-RUN] AIFF comment {aiff_path.name}: {comment_text}")
        return

    audio = AIFF(aiff_path)
    tags = audio.tags
    if tags is None:
        tags = ID3()
        audio.tags = tags

    tags.delall("COMM")
    tags.add(COMM(encoding=3, lang="eng", desc="essentia", text=[comment_text]))
    tags.save(aiff_path)


def write_itunes_import_script(target_path: Path, aiff_files: list[Path]) -> Path:
    script_path = target_path / "import_to_itunes.ps1"
    lines = [
        "# Manual step: import processed AIFF files into iTunes and create playlist",
        "$playlistName = Read-Host 'Playlist name'",
        "$aiffFiles = @(",
    ]
    lines.extend([f'    "{path}"' for path in aiff_files])
    lines.extend(
        [
            ")",
            "Write-Host 'Import these files in iTunes and add to playlist:' -ForegroundColor Cyan",
            "$aiffFiles | ForEach-Object { Write-Host $_ }",
            "Write-Host \"Suggested playlist name: $playlistName\" -ForegroundColor Yellow",
        ]
    )
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_path


def run_flow(
    target_path: Path | None,
    source_dir: Path,
    default_genre: str,
    interactive_unsure: bool,
    max_tracks: int | None,
    skip_rx10: bool,
    ffmpeg_exe: str,
    rx10_exe: Path,
    rx10_preset: str,
    essentia_exe: Path,
    dry_run: bool,
) -> None:
    selected = select_target_folder(INIT_TARGET_PATH, explicit_target_path=target_path)
    entries = parse_set_file(selected.set_file)

    print(f"\nSelected TARGET_PATH: {selected.target_path}")
    print(f"Selected set file: {selected.set_file}")
    print(f"MP3_SOURCE: {source_dir}")

    matched = match_entries_to_mp3s(
        entries,
        source_dir=source_dir,
        interactive_unsure=interactive_unsure,
    )
    if max_tracks is not None and max_tracks > 0:
        matched = matched[:max_tracks]
    print(f"Matched tracks: {len(matched)} / {len(entries)}")

    run_tagging(matched, default_genre=default_genre, dry_run=dry_run)

    aiff_dir = ensure_aiff_dir(selected.target_path)
    aiff_files = convert_mp3_to_aiff_24bit(matched, aiff_dir=aiff_dir, ffmpeg_exe=ffmpeg_exe, dry_run=dry_run)
    processed_aiff_dir = ensure_processed_aiff_dir(selected.target_path)

    if skip_rx10:
        processed_aiff_files = passthrough_to_processed(aiff_files, output_dir=processed_aiff_dir, dry_run=dry_run)
    else:
        processed_aiff_files = run_rx10_headless(
            aiff_files,
            output_dir=processed_aiff_dir,
            rx10_exe=rx10_exe,
            preset=rx10_preset,
            dry_run=dry_run,
        )

    essentia_jsons = run_essentia(processed_aiff_files, essentia_exe=essentia_exe, dry_run=dry_run)
    for aiff_file, json_file in zip(processed_aiff_files, essentia_jsons):
        summary = extract_essentia_summary(json_file)
        add_comment_to_aiff(aiff_file, summary, dry_run=dry_run)

    import_script = write_itunes_import_script(selected.target_path, processed_aiff_files)
    print(f"\nGenerated iTunes helper script: {import_script}")
    print("Please run it after this flow to import files and build your playlist.")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the high-level DJ set prep flow.")
    parser.add_argument(
        "--target-path",
        type=Path,
        default=None,
        help="Optional explicit target set folder. If omitted, script suggests from INIT_TARGET_PATH.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=MP3_SOURCE,
        help="Source MP3 library root.",
    )
    parser.add_argument("--default-genre", default="Electronic")
    parser.add_argument(
        "--interactive-unsure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prompt to choose candidate file for uncertain matches (default: enabled).",
    )
    parser.add_argument(
        "--max-tracks",
        type=int,
        default=None,
        help="Optional cap on number of matched tracks to process (e.g. 1 for smoke test).",
    )
    parser.add_argument("--ffmpeg-exe", default="ffmpeg")
    parser.add_argument("--rx10-exe", type=Path, default=DEFAULT_RX10_EXE)
    parser.add_argument("--rx10-preset", default=DEFAULT_RX_PRESET)
    parser.add_argument(
        "--skip-rx10",
        action="store_true",
        help="Skip RX10 stage and pass converted AIFF files directly into aiffProcessed.",
    )
    parser.add_argument("--essentia-exe", type=Path, default=DEFAULT_ESSENTIA_EXE)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run_flow(
        target_path=args.target_path,
        source_dir=args.source_dir,
        default_genre=args.default_genre,
        interactive_unsure=args.interactive_unsure,
        max_tracks=args.max_tracks,
        skip_rx10=args.skip_rx10,
        ffmpeg_exe=args.ffmpeg_exe,
        rx10_exe=args.rx10_exe,
        rx10_preset=args.rx10_preset,
        essentia_exe=args.essentia_exe,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
