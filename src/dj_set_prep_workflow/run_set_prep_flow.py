from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mutagen.aiff import AIFF
from mutagen.id3 import COMM, ID3, TALB, TCON, TIT2, TPE1, TPE2, TDRC
from mutagen.mp3 import MP3

from .tag_set_mp3s import (
    TrackEntry,
    find_mp3_files,
    parse_set_file,
    select_match_mp3,
)

DEFAULT_PREP_ROOT = Path(r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep")
DEFAULT_ESSENTIA_EXE = Path(
    r"D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
)
DEFAULT_PREMASTER_EXE = Path(
    r"C:\Program Files\iZotope\RX 10 Audio Editor\win64\RX10Headless.exe"
)
DEFAULT_PREMASTER_PRESET = "DJ Set Prep"


@dataclass(slots=True)
class PrepPaths:
    root: Path
    artwork: Path
    converted_aiff: Path
    logs: Path
    metadata: Path
    processed_aiff: Path
    source_mp3s: Path
    raw_metadata_file: Path
    processed_metadata_file: Path


def build_prep_paths(prep_root: Path) -> PrepPaths:
    metadata_dir = prep_root / "Metadata"
    return PrepPaths(
        root=prep_root,
        artwork=prep_root / "Artwork",
        converted_aiff=prep_root / "ConvertedAIFF",
        logs=prep_root / "Logs",
        metadata=metadata_dir,
        processed_aiff=prep_root / "ProcessedAIFF",
        source_mp3s=prep_root / "SourceMP3s",
        raw_metadata_file=metadata_dir / "raw-track-metadata.txt",
        processed_metadata_file=metadata_dir / "processed-track-metadata.txt",
    )


def ensure_prep_dirs(paths: PrepPaths) -> None:
    for directory in [
        paths.root,
        paths.artwork,
        paths.converted_aiff,
        paths.logs,
        paths.metadata,
        paths.processed_aiff,
        paths.source_mp3s,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def first_tag_value(tag_values: dict[str, list[str]], key: str) -> str | None:
    values = tag_values.get(key)
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None


def id3_to_dict(tags: ID3 | None) -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    if tags is None:
        return payload

    for key in tags.keys():
        frame = tags.get(key)
        text_values = getattr(frame, "text", None)
        if text_values:
            payload[key] = [str(item) for item in text_values]
    return payload


def read_source_mp3_tags(mp3_path: Path) -> dict[str, list[str]]:
    audio = MP3(mp3_path)
    return id3_to_dict(audio.tags)


def resolve_metadata_file(paths: PrepPaths, set_file: Path | None) -> Path:
    candidate = set_file or paths.raw_metadata_file
    if not candidate.exists():
        raise FileNotFoundError(f"Metadata track list file not found: {candidate}")
    if candidate.stat().st_size == 0:
        raise ValueError(f"Metadata track list is empty: {candidate}")
    return candidate


def run_tagging_on_aiff(
    aiff_path: Path,
    entry: TrackEntry,
    source_tags: dict[str, list[str]],
    essentia_summary: str,
    default_genre: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    base_title = first_tag_value(source_tags, "TIT2") or entry.title.strip() or aiff_path.stem

    label_year_suffix = ""
    if entry.label and entry.year:
        label_year_suffix = f" [{entry.label} {entry.year}]"
    elif entry.label:
        label_year_suffix = f" [{entry.label}]"
    elif entry.year:
        label_year_suffix = f" [{entry.year}]"

    if label_year_suffix and not base_title.lower().endswith(label_year_suffix.lower()):
        final_title = f"{base_title}{label_year_suffix}".strip()
    else:
        final_title = base_title

    artist_value = first_tag_value(source_tags, "TPE1") or entry.artist
    album_artist_value = artist_value
    year_value = first_tag_value(source_tags, "TDRC") or entry.year
    genre_value = first_tag_value(source_tags, "TCON") or default_genre
    album_value = first_tag_value(source_tags, "TALB") or "DJ Set Prep"

    if dry_run:
        print(
            f"[DRY-RUN] AIFF tag {aiff_path.name} -> title='{final_title}', artist='{artist_value}', "
            f"album_artist='{album_artist_value}', year='{year_value}', genre='{genre_value}', comment='{essentia_summary}'"
        )
        result: dict[str, list[str]] = {
            "TIT2": [final_title],
            "TPE1": [artist_value],
            "TPE2": [album_artist_value],
            "COMM:essentia": [essentia_summary],
        }
        if year_value:
            result["TDRC"] = [str(year_value)]
        if genre_value:
            result["TCON"] = [genre_value]
        if album_value:
            result["TALB"] = [album_value]
        return result

    audio = AIFF(aiff_path)
    tags = audio.tags
    if tags is None:
        tags = ID3()
        audio.tags = tags

    tags.setall("TIT2", [TIT2(encoding=3, text=[final_title])])
    tags.setall("TPE1", [TPE1(encoding=3, text=[artist_value])])
    tags.setall("TPE2", [TPE2(encoding=3, text=[album_artist_value])])

    if year_value and not tags.get("TDRC"):
        tags.setall("TDRC", [TDRC(encoding=3, text=[str(year_value)])])
    if genre_value and not tags.get("TCON"):
        tags.setall("TCON", [TCON(encoding=3, text=[genre_value])])
    if album_value and not tags.get("TALB"):
        tags.setall("TALB", [TALB(encoding=3, text=[album_value])])

    tags.delall("COMM")
    tags.add(COMM(encoding=3, lang="eng", desc="essentia", text=[essentia_summary]))
    tags.save(aiff_path)
    return id3_to_dict(tags)


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


def convert_single_mp3_to_aiff_24bit(mp3_path: Path, output_dir: Path, ffmpeg_exe: str, dry_run: bool) -> Path:
    output_path = output_dir / f"{mp3_path.stem}.aiff"
    cmd = [ffmpeg_exe, "-y", "-i", str(mp3_path), "-c:a", "pcm_s24be", str(output_path)]
    if dry_run:
        print(f"[DRY-RUN] ffmpeg: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    return output_path


def run_premaster(
    input_aiff: Path,
    output_dir: Path,
    premaster_exe: Path,
    preset: str,
    skip_premaster: bool,
    dry_run: bool,
) -> Path:
    output_path = output_dir / input_aiff.name

    if skip_premaster:
        if dry_run:
            print(f"[DRY-RUN] Skip pre-master passthrough: {input_aiff} -> {output_path}")
        else:
            shutil.copy2(input_aiff, output_path)
        return output_path

    cmd = [
        str(premaster_exe),
            "--headless",
            "--preset",
            preset,
            "--input",
        str(input_aiff),
            "--output",
            str(output_path),
    ]
    if dry_run:
        print(f"[DRY-RUN] Pre-master: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    return output_path


def run_essentia_single(aiff_file: Path, logs_dir: Path, essentia_exe: Path, dry_run: bool) -> Path:
    json_path = logs_dir / f"{aiff_file.stem}.essentia.json"
    cmd = [str(essentia_exe), str(aiff_file), str(json_path)]
    if dry_run:
        print(f"[DRY-RUN] Essentia: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    return json_path


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


def write_processed_metadata(processed_metadata_file: Path, records: list[dict[str, Any]], dry_run: bool) -> None:
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else "")
    if dry_run:
        print(f"[DRY-RUN] Would write metadata records to {processed_metadata_file} ({len(records)} records)")
        return
    processed_metadata_file.write_text(content, encoding="utf-8")


def run_flow(
    prep_root: Path,
    set_file: Path | None,
    source_dir: Path | None,
    default_genre: str,
    interactive_unsure: bool,
    max_tracks: int | None,
    skip_premaster: bool,
    ffmpeg_exe: str,
    premaster_exe: Path,
    premaster_preset: str,
    essentia_exe: Path,
    dry_run: bool,
) -> None:
    paths = build_prep_paths(prep_root)
    ensure_prep_dirs(paths)

    resolved_set_file = resolve_metadata_file(paths, set_file=set_file)
    resolved_source_dir = source_dir or paths.source_mp3s
    entries = parse_set_file(resolved_set_file)

    print(f"\nDJ-SET-PREP root: {paths.root}")
    print(f"Metadata input: {resolved_set_file}")
    print(f"Source MP3s: {resolved_source_dir}")
    print(f"ConvertedAIFF: {paths.converted_aiff}")
    print(f"ProcessedAIFF: {paths.processed_aiff}")
    print(f"Logs: {paths.logs}")

    matched = match_entries_to_mp3s(
        entries,
        source_dir=resolved_source_dir,
        interactive_unsure=interactive_unsure,
    )
    if max_tracks is not None and max_tracks > 0:
        matched = matched[:max_tracks]
    print(f"Matched tracks: {len(matched)} / {len(entries)}")

    processed_records: list[dict[str, Any]] = []
    for idx, (entry, mp3_path) in enumerate(matched, start=1):
        print(f"\n[{idx}/{len(matched)}] Processing: {mp3_path.name}")
        source_tags = read_source_mp3_tags(mp3_path)

        converted_path = convert_single_mp3_to_aiff_24bit(
            mp3_path,
            output_dir=paths.converted_aiff,
            ffmpeg_exe=ffmpeg_exe,
            dry_run=dry_run,
        )
        processed_path = run_premaster(
            converted_path,
            output_dir=paths.processed_aiff,
            premaster_exe=premaster_exe,
            preset=premaster_preset,
            skip_premaster=skip_premaster,
            dry_run=dry_run,
        )
        essentia_json = run_essentia_single(
            processed_path,
            logs_dir=paths.logs,
            essentia_exe=essentia_exe,
            dry_run=dry_run,
        )
        essentia_summary = extract_essentia_summary(essentia_json)
        final_tags = run_tagging_on_aiff(
            processed_path,
            entry=entry,
            source_tags=source_tags,
            essentia_summary=essentia_summary,
            default_genre=default_genre,
            dry_run=dry_run,
        )

        processed_records.append(
            {
                "source_mp3": str(mp3_path),
                "converted_aiff": str(converted_path),
                "processed_aiff": str(processed_path),
                "essentia_json": str(essentia_json),
                "set_metadata": {
                    "title": entry.title,
                    "artist": entry.artist,
                    "label": entry.label,
                    "year": entry.year,
                },
                "source_tags": source_tags,
                "processed_tags": final_tags,
                "essentia_comment": essentia_summary,
            }
        )

    write_processed_metadata(paths.processed_metadata_file, processed_records, dry_run=dry_run)
    print(f"\nProcessed metadata output: {paths.processed_metadata_file}")
    print("Flow complete (stopping before iTunes import/playlist stage).")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the high-level DJ set prep flow.")
    parser.add_argument(
        "--prep-root",
        type=Path,
        default=DEFAULT_PREP_ROOT,
        help="Root folder for DJ-SET-PREP structure.",
    )
    parser.add_argument(
        "--set-file",
        type=Path,
        default=None,
        help="Optional path to metadata text file. Defaults to Metadata/raw-track-metadata.txt under prep root.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional override for source MP3 root. Defaults to SourceMP3s under prep root.",
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
    parser.add_argument("--premaster-exe", type=Path, default=DEFAULT_PREMASTER_EXE)
    parser.add_argument("--premaster-preset", default=DEFAULT_PREMASTER_PRESET)
    parser.add_argument(
        "--skip-premaster",
        action="store_true",
        help="Skip pre-master stage and copy ConvertedAIFF files into ProcessedAIFF.",
    )
    parser.add_argument("--essentia-exe", type=Path, default=DEFAULT_ESSENTIA_EXE)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run_flow(
        prep_root=args.prep_root,
        set_file=args.set_file,
        source_dir=args.source_dir,
        default_genre=args.default_genre,
        interactive_unsure=args.interactive_unsure,
        max_tracks=args.max_tracks,
        skip_premaster=args.skip_premaster,
        ffmpeg_exe=args.ffmpeg_exe,
        premaster_exe=args.premaster_exe,
        premaster_preset=args.premaster_preset,
        essentia_exe=args.essentia_exe,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
