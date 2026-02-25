from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TCON, TDRC, COMM

from .tag_set_mp3s import TrackEntry, normalize, parse_set_file

DEFAULT_PREP_ROOT = Path(r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep")
DEFAULT_REAPER_EXE = Path(r"C:\Program Files\REAPER (x64)\reaper.exe")
DEFAULT_ESSENTIA_EXE = Path(
    r"D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}


@dataclass(slots=True)
class PrepPaths:
    root: Path
    artwork: Path
    converted_aiff: Path
    logs: Path
    metadata: Path
    processed_aiff: Path
    source_files: Path
    templates: Path
    raw_metadata_file: Path
    processed_metadata_file: Path


@dataclass(slots=True)
class MetadataMatch:
    entry: TrackEntry | None
    source: str


def build_prep_paths(prep_root: Path) -> PrepPaths:
    metadata_dir = prep_root / "Metadata"
    return PrepPaths(
        root=prep_root,
        artwork=prep_root / "Artwork",
        converted_aiff=prep_root / "ConvertedFiles",
        logs=prep_root / "Logs",
        metadata=metadata_dir,
        processed_aiff=prep_root / "ProcessedFiles",
        source_files=prep_root / "SourceFiles",
        templates=prep_root / "Templates",
        raw_metadata_file=metadata_dir / "raw-track-metadata.txt",
        processed_metadata_file=metadata_dir / "processed-track-metadata.txt",
    )


def ensure_dirs(paths: PrepPaths) -> None:
    for directory in [
        paths.root,
        paths.artwork,
        paths.converted_aiff,
        paths.logs,
        paths.metadata,
        paths.processed_aiff,
        paths.source_files,
        paths.templates,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def maybe_confirm(confirm_steps: bool, message: str) -> None:
    if confirm_steps:
        input(f"[CONFIRM] {message} Press Enter to continue...")


def list_source_files(source_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in source_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        ]
    )


def extract_tags_dict(audio_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {
        "full_path": str(audio_path),
        "file_name": audio_path.name,
        "file_stem": audio_path.stem,
        "extension": audio_path.suffix.lower(),
    }

    audio = MutagenFile(str(audio_path), easy=True)
    if audio and audio.tags:
        for key, values in dict(audio.tags).items():
            if isinstance(values, list):
                data[key] = [str(value) for value in values]
            else:
                data[key] = [str(values)]

    return data


def append_suffix_to_title(title: str, suffix: str | None) -> str:
    if not suffix:
        return title

    title_norm = normalize(title)
    suffix_norm = normalize(suffix)
    if suffix_norm and title_norm.endswith(suffix_norm):
        return title

    return f"{title} {suffix}".strip()


def find_metadata_match(
    metadata_entries: list[TrackEntry],
    source_tags: dict[str, Any],
    used_entry_indices: set[int],
    fallback_index: int,
) -> MetadataMatch:
    title = str((source_tags.get("title") or [source_tags.get("file_stem", "")])[0]).strip()
    artist = str((source_tags.get("artist") or [""])[0]).strip()

    title_key = normalize(title)
    artist_key = normalize(artist)

    for idx, entry in enumerate(metadata_entries):
        if idx in used_entry_indices:
            continue
        if normalize(entry.title) == title_key and normalize(entry.artist) == artist_key:
            used_entry_indices.add(idx)
            return MetadataMatch(entry=entry, source="title+artist")

    if 0 <= fallback_index < len(metadata_entries) and fallback_index not in used_entry_indices:
        used_entry_indices.add(fallback_index)
        return MetadataMatch(entry=metadata_entries[fallback_index], source="sequential-fallback")

    return MetadataMatch(entry=None, source="none")


def metadata_suffix(entry: TrackEntry | None) -> str | None:
    if not entry:
        return None

    if entry.label and entry.year:
        return f"[{entry.label} {entry.year}]".strip()
    if entry.label:
        return f"[{entry.label.strip()}]"
    if entry.year:
        return f"[{entry.year.strip()}]"
    return None


def convert_to_aiff(source_file: Path, converted_dir: Path, ffmpeg_exe: str, dry_run: bool) -> Path:
    output_path = converted_dir / f"{source_file.stem}.aif"
    cmd = [ffmpeg_exe, "-y", "-i", str(source_file), "-c:a", "pcm_s24be", str(output_path)]
    print(f"[START] Convert -> {output_path.name}")
    if dry_run:
        print(f"[DRY-RUN] ffmpeg: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    print(f"[INFO] Converted AIFF: {output_path}")
    print("[DONE] Convert")
    return output_path


def copy_to_template_input(converted_aiff: Path, templates_dir: Path, dry_run: bool) -> Path:
    template_input = templates_dir / "input.aif"
    print(f"[START] Copy to template input -> {template_input}")
    if dry_run:
        print(f"[DRY-RUN] copy: {converted_aiff} -> {template_input}")
    else:
        shutil.copy2(converted_aiff, template_input)
    print("[DONE] Copy to template input")
    return template_input


def run_reaper_render(
    reaper_exe: Path,
    reaper_project: Path,
    logs_dir: Path,
    file_stem: str,
    dry_run: bool,
) -> Path:
    output_path = logs_dir.parent / "ProcessedFiles" / "output.aif"
    log_path = logs_dir / f"{file_stem}.reaper.log"
    cmd = [str(reaper_exe), "-renderproject", str(reaper_project)]

    print("[START] Reaper render")
    print(f"[INFO] Reaper project: {reaper_project}")
    if dry_run:
        print(f"[DRY-RUN] Reaper: {' '.join(cmd)}")
        print(f"[DRY-RUN] Reaper expected output: {output_path}")
    else:
        print("[INFO] Reaper rendering started (this step can be slow)...")
        started = time.monotonic()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            elapsed = int(time.monotonic() - started)
            print(f"[INFO] Reaper still rendering... {elapsed}s")
            time.sleep(5)

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout, stderr=stderr)

        elapsed = time.monotonic() - started
        log_path.write_text((stdout or "") + "\n" + (stderr or ""), encoding="utf-8")
        print(f"[INFO] Reaper log: {log_path}")
        print(f"[INFO] Reaper output (expected): {output_path}")
        print(f"[INFO] Reaper duration: {elapsed:.1f}s")
    print("[DONE] Reaper render")
    return output_path


def rename_render_output(processed_dir: Path, target_stem: str, dry_run: bool) -> Path:
    src = processed_dir / "output.aif"
    dst = processed_dir / f"{target_stem}.aif"
    print(f"[START] Rename render output -> {dst.name}")
    if dry_run:
        print(f"[DRY-RUN] rename: {src} -> {dst}")
    else:
        if not src.exists():
            raise FileNotFoundError(f"Expected Reaper output not found: {src}")
        if dst.exists():
            dst.unlink()
        src.rename(dst)
    print(f"[INFO] Rendered AIFF: {dst}")
    print("[DONE] Rename render output")
    return dst


def run_essentia_single(
    rendered_file: Path,
    logs_dir: Path,
    essentia_exe: Path,
    dry_run: bool,
) -> Path:
    json_path = logs_dir / f"{rendered_file.stem}.essentia.json"
    log_path = logs_dir / f"{rendered_file.stem}.essentia.log"
    cmd = [str(essentia_exe), str(rendered_file), str(json_path)]

    print("[START] Essentia")
    print(f"[INFO] Essentia input: {rendered_file}")
    print(f"[INFO] Essentia output JSON: {json_path}")
    if dry_run:
        print(f"[DRY-RUN] Essentia: {' '.join(cmd)}")
    else:
        print("[INFO] Essentia processing started (this step can be slow)...")
        started = time.monotonic()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            elapsed = int(time.monotonic() - started)
            print(f"[INFO] Essentia still running... {elapsed}s")
            time.sleep(5)

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout, stderr=stderr)

        elapsed = time.monotonic() - started
        log_path.write_text((stdout or "") + "\n" + (stderr or ""), encoding="utf-8")
        print(f"[INFO] Essentia log: {log_path}")
        print(f"[INFO] Essentia duration: {elapsed:.1f}s")
    print(f"[INFO] Essentia JSON: {json_path}")
    print("[DONE] Essentia")
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

    key_text = None
    scale_key = str(scale).lower() if scale else None
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

    parts = []
    if key_text:
        parts.append(f"Key: {key_text}")
    if chords_text:
        parts.append(f"Chords: {chords_text}")
    if energy_text:
        parts.append(f"Energy: {energy_text}")
    
    return " ".join(parts) if parts else ""


def write_tags_to_processed_aiff(
    rendered_aiff: Path,
    source_tags: dict[str, Any],
    metadata_entry: TrackEntry | None,
    essentia_comment: str,
    default_genre: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    base_title = str((source_tags.get("title") or [source_tags.get("file_stem", rendered_aiff.stem)])[0]).strip()
    suffix = metadata_suffix(metadata_entry)
    final_title = append_suffix_to_title(base_title, suffix)

    artist_value = str((source_tags.get("artist") or [metadata_entry.artist if metadata_entry else ""])[0]).strip()
    album_artist_value = artist_value

    source_year = str((source_tags.get("date") or source_tags.get("year") or [""])[0]).strip()
    metadata_year = metadata_entry.year if metadata_entry else None
    year_value = source_year or (metadata_year or "")

    genre_value = str((source_tags.get("genre") or [default_genre])[0]).strip() or default_genre
    album_value = str((source_tags.get("album") or ["DJ Set Prep"])[0]).strip() or "DJ Set Prep"

    result_tags: dict[str, list[str]] = {
        "title": [final_title],
        "artist": [artist_value],
        "albumartist": [album_artist_value],
        "album": [album_value],
        "genre": [genre_value],
        "comment": [essentia_comment],
    }
    if year_value:
        result_tags["date"] = [year_value]

    print("[START] Write tags to processed AIFF")
    if dry_run:
        print(f"[DRY-RUN] tags for {rendered_aiff.name}: {json.dumps(result_tags, ensure_ascii=False)}")
        print("[DONE] Write tags to processed AIFF")
        return result_tags

    try:
        audio = ID3(str(rendered_aiff))
    except Exception:
        audio = ID3()

    audio.delete(str(rendered_aiff))
    audio.add(TIT2(encoding=3, text=final_title))
    audio.add(TPE1(encoding=3, text=artist_value))
    audio.add(TPE2(encoding=3, text=album_artist_value))
    audio.add(TALB(encoding=3, text=album_value))
    audio.add(TCON(encoding=3, text=genre_value))
    if year_value:
        audio.add(TDRC(encoding=3, text=year_value))
    if essentia_comment:
        audio.add(COMM(encoding=3, lang="eng", desc="", text=essentia_comment))

    audio.save(str(rendered_aiff), v2_version=3)

    print("[DONE] Write tags to processed AIFF")
    return result_tags


def write_processed_metadata(records: list[dict[str, Any]], output_file: Path, dry_run: bool) -> None:
    print("[START] Write processed metadata file")
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else "")
    if dry_run:
        print(f"[DRY-RUN] would write {len(records)} records to {output_file}")
    else:
        output_file.write_text(content, encoding="utf-8")
    print("[DONE] Write processed metadata file")


def run_flow(
    prep_root: Path,
    set_file: Path | None,
    source_dir: Path | None,
    ffmpeg_exe: str,
    reaper_exe: Path,
    reaper_project: Path | None,
    essentia_exe: Path,
    default_genre: str,
    max_tracks: int | None,
    dry_run: bool,
    confirm_steps: bool,
    stop_after_render: bool,
) -> None:
    paths = build_prep_paths(prep_root)
    ensure_dirs(paths)

    resolved_source_dir = source_dir or paths.source_files
    resolved_set_file = set_file or paths.raw_metadata_file
    resolved_reaper_project = reaper_project or (paths.templates / "DJ Set Prep.rpp")

    if not resolved_set_file.exists():
        raise FileNotFoundError(f"Metadata file not found: {resolved_set_file}")
    if not resolved_reaper_project.exists():
        raise FileNotFoundError(f"Reaper project not found: {resolved_reaper_project}")

    metadata_entries = parse_set_file(resolved_set_file)
    source_files = list_source_files(resolved_source_dir)

    if not source_files:
        raise FileNotFoundError(f"No supported source audio files found in: {resolved_source_dir}")

    if max_tracks is not None and max_tracks > 0:
        source_files = source_files[:max_tracks]

    print(f"DJ-SET-PREP root: {paths.root}")
    print(f"Source files dir: {resolved_source_dir}")
    print(f"Metadata file: {resolved_set_file}")
    print(f"Reaper project: {resolved_reaper_project}")
    print(f"Source files discovered: {len(source_files)}")

    processed_records: list[dict[str, Any]] = []
    used_entry_indices: set[int] = set()

    for idx, source_file in enumerate(source_files, start=1):
        print(f"\n=== [{idx}/{len(source_files)}] Processing {source_file.name} ===")

        source_tags = extract_tags_dict(source_file)
        print("[INFO] Extracted tags dictionary:")
        print(json.dumps(source_tags, ensure_ascii=False, indent=2))
        maybe_confirm(confirm_steps, "After tag extraction")

        converted_aiff = convert_to_aiff(source_file, paths.converted_aiff, ffmpeg_exe=ffmpeg_exe, dry_run=dry_run)
        maybe_confirm(confirm_steps, "After conversion to AIFF")

        copy_to_template_input(converted_aiff, paths.templates, dry_run=dry_run)
        maybe_confirm(confirm_steps, "After copying template input.aif")

        run_reaper_render(
            reaper_exe=reaper_exe,
            reaper_project=resolved_reaper_project,
            logs_dir=paths.logs,
            file_stem=source_file.stem,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After Reaper render")

        rendered_aiff = rename_render_output(paths.processed_aiff, target_stem=source_file.stem, dry_run=dry_run)
        maybe_confirm(confirm_steps, "After renaming rendered output")

        if stop_after_render:
            print("[INFO] Stop-after-render enabled. Skipping Essentia, tagging, and metadata output.")
            print("\nFlow complete.")
            return

        essentia_json = run_essentia_single(
            rendered_file=rendered_aiff,
            logs_dir=paths.logs,
            essentia_exe=essentia_exe,
            dry_run=dry_run,
        )
        essentia_comment = extract_essentia_summary(essentia_json)
        print(f"[INFO] Essentia comment: {essentia_comment}")
        maybe_confirm(confirm_steps, "After Essentia extraction")

        metadata_match = find_metadata_match(
            metadata_entries,
            source_tags=source_tags,
            used_entry_indices=used_entry_indices,
            fallback_index=idx - 1,
        )
        print(f"[INFO] Metadata match source: {metadata_match.source}")

        processed_tags = write_tags_to_processed_aiff(
            rendered_aiff,
            source_tags=source_tags,
            metadata_entry=metadata_match.entry,
            essentia_comment=essentia_comment,
            default_genre=default_genre,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After writing processed AIFF tags")

        print(
            "[INFO] Audio processing summary: "
            f"converted='{converted_aiff.name}', rendered='{rendered_aiff.name}'"
        )
        print(f"[INFO] essentia='{essentia_json.name}'")

        processed_records.append(
            {
                "source": {
                    "full_path": str(source_file),
                    "file_name": source_file.name,
                    "file_stem": source_file.stem,
                },
                "converted_aiff": str(converted_aiff),
                "template_input": str(paths.templates / "input.aif"),
                "processed_aiff": str(rendered_aiff),
                "essentia_json": str(essentia_json),
                "metadata_match_source": metadata_match.source,
                "metadata_entry": {
                    "title": metadata_match.entry.title if metadata_match.entry else None,
                    "artist": metadata_match.entry.artist if metadata_match.entry else None,
                    "label": metadata_match.entry.label if metadata_match.entry else None,
                    "year": metadata_match.entry.year if metadata_match.entry else None,
                },
                "source_tags": source_tags,
                "processed_tags": processed_tags,
                "essentia_comment": essentia_comment,
            }
        )

    write_processed_metadata(processed_records, paths.processed_metadata_file, dry_run=dry_run)
    print("\nFlow complete.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DJ set prep workflow on Sourcefiles.")
    parser.add_argument("--prep-root", type=Path, default=DEFAULT_PREP_ROOT, help="DJ-SET-PREP root directory.")
    parser.add_argument(
        "--set-file",
        type=Path,
        default=None,
        help="Optional metadata file path. Default: Metadata/raw-track-metadata.txt under prep root.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional source audio directory. Default: SourceFiles under prep root.",
    )
    parser.add_argument("--ffmpeg-exe", default="ffmpeg")
    parser.add_argument("--reaper-exe", type=Path, default=DEFAULT_REAPER_EXE)
    parser.add_argument(
        "--reaper-project",
        type=Path,
        default=None,
        help="Optional Reaper project path. Default: Templates/DJ Set Prep.rpp under prep root.",
    )
    parser.add_argument("--essentia-exe", type=Path, default=DEFAULT_ESSENTIA_EXE)
    parser.add_argument("--default-genre", default="Electronic")
    parser.add_argument("--max-tracks", type=int, default=None)
    parser.add_argument("--confirm-steps", action="store_true", help="Pause for confirmation after each stage.")
    parser.add_argument(
        "--stop-after-render",
        action="store_true",
        help="Exit after render stage (skip Essentia, tagging, and processed metadata file).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run_flow(
        prep_root=args.prep_root,
        set_file=args.set_file,
        source_dir=args.source_dir,
        ffmpeg_exe=args.ffmpeg_exe,
        reaper_exe=args.reaper_exe,
        reaper_project=args.reaper_project,
        essentia_exe=args.essentia_exe,
        default_genre=args.default_genre,
        max_tracks=args.max_tracks,
        dry_run=args.dry_run,
        confirm_steps=args.confirm_steps,
        stop_after_render=args.stop_after_render,
    )


if __name__ == "__main__":
    main()
