from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    from essentia.standard import MusicExtractor
except ImportError:
    MusicExtractor = None

from mutagen import File as MutagenFile
from mutagen.aiff import AIFF
from mutagen.id3 import APIC, COMM, TALB, TCON, TIT2, TPE1, TPE2, TDRC

from .paths import resolve_default_prep_root
from .tag_set_mp3s import TrackEntry, normalize, parse_set_file

# OS-specific paths
if sys.platform == "win32":
    DEFAULT_PREP_ROOT = resolve_default_prep_root()
    DEFAULT_REAPER_EXE = Path(r"C:\Program Files\REAPER (x64)\reaper.exe")
elif sys.platform == "darwin":
    DEFAULT_PREP_ROOT = resolve_default_prep_root()
    DEFAULT_REAPER_EXE = Path("/Applications/REAPER.app/Contents/MacOS/REAPER")
else:
    # Linux or other
    DEFAULT_PREP_ROOT = resolve_default_prep_root()
    DEFAULT_REAPER_EXE = Path("/usr/bin/reaper")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}

ENERGY_COMPONENT_KEYS = {
    "beat_mean": "rhythm.beats_loudness.mean",
    "beat_stdev": "rhythm.beats_loudness.stdev",
    "punch": "rhythm.beats_loudness.dmean2",
    "spectral_mean": "lowlevel.spectral_energy.mean",
    "spectral_max": "lowlevel.spectral_energy.max",
    "dance": "rhythm.danceability",
    "chords_strength": "tonal.chords_strength.mean",
    "chords_change_rate": "tonal.chords_changes_rate",
}

FALLBACK_SCALER = {
    "beat": {"low": 0.0, "high": 0.18},
    "punch": {"low": 0.0, "high": 0.08},
    "spectral": {"low": 0.0, "high": 0.25},
    "dance": {"low": 0.0, "high": 3.0},
    "harmonic": {"low": 0.0, "high": 0.5},
}

ENERGY_WEIGHTS = {
    "beat": 0.40,
    "punch": 0.10,
    "spectral": 0.20,
    "dance": 0.15,
    "harmonic": 0.15,
}

REAPER_ITEM_LENGTH_RE = re.compile(r'(?m)^(\s+LENGTH)\s+[-+0-9.eE]+$')
REAPER_RENDER_RANGE_RE = re.compile(r'(?m)^(\s+RENDER_RANGE)\s+\d+(.*)$')
REAPER_SELECTION_RE = re.compile(r'(?m)^(\s+SELECTION)\s+[-+0-9.eE]+\s+[-+0-9.eE]+$')
REAPER_SELECTION2_RE = re.compile(r'(?m)^(\s+SELECTION2)\s+[-+0-9.eE]+\s+[-+0-9.eE]+$')
REAPER_RENDER_FILE_RE = re.compile(r'(?m)^(\s+RENDER_FILE)\s+".*"$')
REAPER_SOURCE_FILE_RE = re.compile(r'(?ms)(<SOURCE WAVE\s*\n\s+FILE)\s+".*?"')

CAMERLOT_MAP = {
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


@dataclass(slots=True)
class PrepPaths:
    root: Path
    artwork: Path
    converted_aiff: Path
    logs: Path
    metadata: Path
    processed_aiff: Path
    tagged_aiff: Path
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
        tagged_aiff=prep_root / "TaggedFiles",
        source_files=prep_root / "SourceFiles",
        templates=prep_root / "Templates",
        raw_metadata_file=metadata_dir / "raw-track-metadata.csv",
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
        paths.tagged_aiff,
        paths.source_files,
        paths.templates,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def clear_directory(path: Path, dry_run: bool) -> None:
    print(f"[START] Clear directory -> {path}")
    if dry_run:
        print(f"[DRY-RUN] clear directory: {path}")
    else:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    print("[DONE] Clear directory")


def clean_working_directories(paths: PrepPaths, dry_run: bool) -> None:
    # Keep Logs and Metadata intact; clear only per-run outputs.
    clear_directory(paths.converted_aiff, dry_run=dry_run)
    clear_directory(paths.processed_aiff, dry_run=dry_run)
    clear_directory(paths.tagged_aiff, dry_run=dry_run)


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

    # If the title already has a trailing [label yyyy]-style suffix,
    # keep it and do not append another metadata suffix.
    if re.search(r"\[[^\]]*\b(?:19|20)\d{2}\b[^\]]*\]\s*$", title):
        return title

    title_norm = normalize(title)
    suffix_norm = normalize(suffix)
    if suffix_norm and title_norm.endswith(suffix_norm):
        return title

    # Avoid duplicate title suffixes when title already ends with a bracketed
    # label/year variant that normalizes to the same text.
    bracketed_tail = re.search(r"\[(?P<inner>[^\]]+)\]\s*$", title)
    if bracketed_tail and normalize(bracketed_tail.group("inner")) == suffix_norm:
        return title

    return f"{title} {suffix}".strip()


def read_artwork_frames(source_file: Path, rendered_aiff: Path) -> list[APIC]:
    frames: list[APIC] = []

    source_audio = MutagenFile(str(source_file))
    if source_audio is not None and getattr(source_audio, "tags", None) is not None:
        source_tags = source_audio.tags
        if hasattr(source_tags, "getall"):
            frames.extend(source_tags.getall("APIC"))

    rendered_audio = AIFF(rendered_aiff)
    if rendered_audio.tags is not None:
        frames.extend(rendered_audio.tags.getall("APIC"))

    return frames


def find_metadata_match(
    metadata_entries: list[TrackEntry],
    source_tags: dict[str, Any],
    used_entry_indices: set[int],
    fallback_index: int,
) -> MetadataMatch:
    title = str((source_tags.get("title") or [source_tags.get("file_stem", "")])[0]).strip()
    artist = str((source_tags.get("artist") or [""])[0]).strip()
    source_stem = str(source_tags.get("file_stem", "")).strip()

    title_key = normalize(title)
    artist_key = normalize(artist)
    source_stem_key = normalize(source_stem)

    if source_stem_key:
        for idx, entry in enumerate(metadata_entries):
            if idx in used_entry_indices or not entry.filename:
                continue
            if normalize(Path(entry.filename).stem) == source_stem_key:
                used_entry_indices.add(idx)
                return MetadataMatch(entry=entry, source="filename")

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
    output_path = converted_dir / f"{source_file.stem}.aiff"
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
    template_input = templates_dir / "input.aiff"
    print(f"[START] Copy to template input -> {template_input}")
    if dry_run:
        print(f"[DRY-RUN] copy: {converted_aiff} -> {template_input}")
    else:
        shutil.copy2(converted_aiff, template_input)
    print("[DONE] Copy to template input")
    return template_input


def _format_reaper_value(value: float) -> str:
    formatted = f"{value:.15f}".rstrip("0").rstrip(".")
    return formatted or "0"


def get_audio_duration_seconds(audio_file: Path) -> float:
    audio = MutagenFile(audio_file)
    if audio is None or getattr(audio, "info", None) is None:
        raise ValueError(f"Unable to read audio metadata for duration: {audio_file}")

    duration = float(getattr(audio.info, "length", 0.0) or 0.0)
    if duration <= 0:
        raise ValueError(f"Audio duration must be positive for Reaper render: {audio_file}")
    return duration


def sync_reaper_project_to_input(
    reaper_project: Path,
    input_audio: Path,
    output_audio: Path,
    dry_run: bool,
) -> float:
    duration = get_audio_duration_seconds(input_audio)
    formatted_duration = _format_reaper_value(duration)

    print(f"[START] Sync Reaper project -> {reaper_project.name}")
    print(f"[INFO] Reaper input source: {input_audio}")
    print(f"[INFO] Reaper render target: {output_audio}")
    print(f"[INFO] Reaper item duration: {formatted_duration}s")

    if dry_run:
        print("[DRY-RUN] would update Reaper project source path, item length, and time selection")
        print("[DONE] Sync Reaper project")
        return duration

    project_text = reaper_project.read_text(encoding="utf-8")

    replacements: list[tuple[re.Pattern[str], str]] = [
        (REAPER_RENDER_FILE_RE, rf'\1 "{output_audio}"'),
        (REAPER_RENDER_RANGE_RE, r'\1 4\2'),
        (REAPER_SELECTION_RE, rf'\1 0 {formatted_duration}'),
        (REAPER_SELECTION2_RE, rf'\1 0 {formatted_duration}'),
        (REAPER_ITEM_LENGTH_RE, rf'\1 {formatted_duration}'),
        (REAPER_SOURCE_FILE_RE, rf'\1 "{input_audio}"'),
    ]

    updated_text = project_text
    for pattern, replacement in replacements:
        updated_text, count = pattern.subn(replacement, updated_text, count=1)
        if count != 1:
            raise ValueError(f"Expected exactly one match for Reaper project update: {pattern.pattern}")

    reaper_project.write_text(updated_text, encoding="utf-8")
    print("[DONE] Sync Reaper project")
    return duration


def run_reaper_render(
    reaper_exe: Path,
    reaper_project: Path,
    templates_dir: Path,
    logs_dir: Path,
    file_stem: str,
    dry_run: bool,
) -> Path:
    output_path = templates_dir / "output.aif"
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


def rename_render_output(templates_dir: Path, processed_dir: Path, target_stem: str, dry_run: bool) -> Path:
    candidate_sources = [templates_dir / "output.aif", templates_dir / "output.aiff"]
    dst = processed_dir / f"{target_stem}.aif"
    print(f"[START] Rename render output -> {dst.name}")
    if dry_run:
        print(f"[DRY-RUN] rename: {candidate_sources[0]} -> {dst}")
    else:
        src = next((path for path in candidate_sources if path.exists()), None)
        if src is None:
            expected = ", ".join(str(path) for path in candidate_sources)
            raise FileNotFoundError(f"Expected Reaper output not found. Checked: {expected}")
        if dst.exists():
            dst.unlink()
        src.rename(dst)
    print(f"[INFO] Rendered AIFF: {dst}")
    print("[DONE] Rename render output")
    return dst


def copy_processed_to_tagged(processed_aiff: Path, tagged_dir: Path, dry_run: bool) -> Path:
    tagged_aiff = tagged_dir / processed_aiff.name
    print(f"[START] Copy tagged AIFF -> {tagged_aiff.name}")
    if dry_run:
        print(f"[DRY-RUN] copy: {processed_aiff} -> {tagged_aiff}")
    else:
        shutil.copy2(processed_aiff, tagged_aiff)
    print(f"[INFO] Tagged AIFF: {tagged_aiff}")
    print("[DONE] Copy tagged AIFF")
    return tagged_aiff


def run_essentia_single(
    rendered_file: Path,
    logs_dir: Path,
    dry_run: bool,
) -> Path:
    json_path = logs_dir / f"{rendered_file.stem}.essentia.json"

    print("[START] Essentia")
    print(f"[INFO] Essentia input: {rendered_file}")
    print(f"[INFO] Essentia output JSON: {json_path}")
    
    if dry_run:
        print("[DRY-RUN] Would extract audio features using essentia MusicExtractor")
    else:
        if MusicExtractor is None:
            raise ImportError(
                "Essentia Python library not found. Install with: poetry install"
            )
        
        print("[INFO] Essentia processing started (this step can be slow)...")
        started = time.monotonic()
        
        try:
            extractor = MusicExtractor()
            results = extractor(str(rendered_file))

            # Essentia can return a mapping or a tuple of Pool objects (features, frames).
            if isinstance(results, tuple):
                features = results[0] if results else None
            else:
                features = results

            if features is None:
                raise TypeError(f"Unsupported Essentia result type: {type(results)!r}")
            
            # Convert essentia results to JSON-serializable format
            essence_dict = {}
            if hasattr(features, "items"):
                iterator = features.items()
            elif hasattr(features, "descriptorNames") and callable(features.descriptorNames):
                iterator = ((name, features[name]) for name in features.descriptorNames())
            else:
                raise TypeError(f"Unsupported Essentia feature container: {type(features)!r}")

            for key, value in iterator:
                if hasattr(value, "tolist"):
                    essence_dict[key] = value.tolist()
                else:
                    essence_dict[key] = float(value) if isinstance(value, (int, float)) else str(value)
            
            json_path.write_text(json.dumps(essence_dict, indent=2), encoding="utf-8")
            
            elapsed = time.monotonic() - started
            print(f"[INFO] Essentia duration: {elapsed:.1f}s")
        except Exception as e:
            raise RuntimeError(f"Essentia extraction failed: {e}")
    
    print(f"[INFO] Essentia JSON: {json_path}")
    print("[DONE] Essentia")
    return json_path


def _numeric_value(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, (list, tuple)):
        numeric_values: list[float] = []
        for item in value:
            try:
                numeric_values.append(float(item))
            except (TypeError, ValueError):
                continue
        if not numeric_values:
            return None
        if key.endswith("max"):
            return float(np.max(numeric_values))
        if key.endswith("stdev"):
            return float(np.std(numeric_values))
        return float(np.mean(numeric_values))

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _camelot_key(key: Any, scale: Any) -> str | None:
    if not key and not scale:
        return None

    scale_key = str(scale).lower() if scale else None
    if key and scale_key in CAMERLOT_MAP:
        mapped = CAMERLOT_MAP[scale_key].get(str(key))
        if mapped:
            return mapped
    return "unknown"


def _build_energy_components(payload: dict[str, Any]) -> dict[str, float] | None:
    beat_mean = _numeric_value(payload, ENERGY_COMPONENT_KEYS["beat_mean"])
    beat_stdev = _numeric_value(payload, ENERGY_COMPONENT_KEYS["beat_stdev"])
    punch = _numeric_value(payload, ENERGY_COMPONENT_KEYS["punch"])
    spectral_mean = _numeric_value(payload, ENERGY_COMPONENT_KEYS["spectral_mean"])
    spectral_max = _numeric_value(payload, ENERGY_COMPONENT_KEYS["spectral_max"])
    dance = _numeric_value(payload, ENERGY_COMPONENT_KEYS["dance"])
    chords_strength = _numeric_value(payload, ENERGY_COMPONENT_KEYS["chords_strength"])
    chords_change_rate = _numeric_value(payload, ENERGY_COMPONENT_KEYS["chords_change_rate"])

    values = [
        beat_mean,
        beat_stdev,
        punch,
        spectral_mean,
        spectral_max,
        dance,
        chords_strength,
        chords_change_rate,
    ]
    if any(value is None for value in values):
        return None

    assert beat_mean is not None
    assert beat_stdev is not None
    assert punch is not None
    assert spectral_mean is not None
    assert spectral_max is not None
    assert dance is not None
    assert chords_strength is not None
    assert chords_change_rate is not None

    cv = beat_stdev / (beat_mean + 1e-9)
    consistency = 1.0 / (1.0 + cv)
    beat = (beat_mean * 0.6) + (consistency * 0.4)
    spectral = (spectral_mean * 0.7) + (spectral_max * 0.3)
    harmonic = (chords_strength * 0.5) + (chords_change_rate * 0.5)

    return {
        "beat": beat,
        "punch": punch,
        "spectral": spectral,
        "dance": dance,
        "harmonic": harmonic,
    }


def _fit_scaler(components: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    if len(components) < 2:
        return FALLBACK_SCALER

    scaler: dict[str, dict[str, float]] = {}
    for key, bounds in FALLBACK_SCALER.items():
        values = [item[key] for item in components]
        low = float(np.percentile(values, 5))
        high = float(np.percentile(values, 95))
        if high <= low:
            scaler[key] = bounds
        else:
            scaler[key] = {"low": low, "high": high}
    return scaler


def _score_energy(components: dict[str, float], scaler: dict[str, dict[str, float]]) -> int:
    total = 0.0
    for key, weight in ENERGY_WEIGHTS.items():
        low = scaler[key]["low"]
        high = scaler[key]["high"]
        normalized = np.clip((components[key] - low) / (high - low + 1e-9), 0.0, 1.0)
        total += weight * float(normalized)
    return int(round(total * 100.0))


def summarize_essentia_payload(payload: dict[str, Any], energy_value: int | None = None) -> dict[str, Any]:
    key_text = _camelot_key(payload.get("tonal.key_temperley.key"), payload.get("tonal.key_temperley.scale"))
    chords_text = _camelot_key(payload.get("tonal.chords_key"), payload.get("tonal.chords_scale"))

    bpm_text = None
    bpm_value = _numeric_value(payload, "rhythm.bpm")
    if bpm_value is not None:
        bpm_text = str(int(round(float(bpm_value))))

    energy_text = None
    if energy_value is not None:
        energy_text = str(int(energy_value))
    else:
        danceability = _numeric_value(payload, "rhythm.danceability")
        if danceability is not None:
            energy_text = str(int(round(min(float(danceability), 10))))

    parts = [
        f"key={key_text}" if key_text else None,
        f"chords={chords_text}" if chords_text else None,
        f"energy={energy_text}" if energy_text else None,
    ]
    filtered = [part for part in parts if part]
    comment = ";".join(filtered) if filtered else "no-summary"

    return {
        "comment": comment,
        "key": key_text,
        "chords": chords_text,
        "bpm": bpm_text,
        "energy": energy_text,
    }


def build_essentia_enrichment(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per_track_payload: dict[str, dict[str, Any]] = {}
    components_by_track: dict[str, dict[str, float]] = {}

    for record in records:
        json_path = Path(str(record.get("essentia_json", "")))
        if not json_path.exists():
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            continue

        track_key = str(record.get("processed_aiff", json_path.stem))
        per_track_payload[track_key] = payload
        components = _build_energy_components(payload)
        if components is not None:
            components_by_track[track_key] = components

    scaler = _fit_scaler(list(components_by_track.values()))

    enrichment: dict[str, dict[str, Any]] = {}
    for track_key, payload in per_track_payload.items():
        energy_value = None
        components = components_by_track.get(track_key)
        if components is not None:
            energy_value = _score_energy(components, scaler)
        enrichment[track_key] = summarize_essentia_payload(payload, energy_value=energy_value)

    return enrichment


def update_essentia_comment_frames(audio_path: Path, comment: str, dry_run: bool) -> None:
    print(f"[START] Update Essentia comment frames -> {audio_path.name}")
    if dry_run:
        print(f"[DRY-RUN] update COMM frames for {audio_path}: {comment}")
        print("[DONE] Update Essentia comment frames")
        return

    audio = AIFF(audio_path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"Failed to initialize ID3 tags for {audio_path}")

    for frame in list(tags.getall("COMM")):
        tags.delall(frame.HashKey)

    tags.add(COMM(encoding=3, lang="eng", desc="", text=[comment]))
    tags.add(COMM(encoding=3, lang="eng", desc="essentia", text=[comment]))
    audio.save()
    print("[DONE] Update Essentia comment frames")


def extract_essentia_summary(json_path: Path) -> str:
    if not json_path.exists():
        return "no-summary"

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return "no-summary"

    return str(summarize_essentia_payload(payload)["comment"])


def write_tags_to_processed_aiff(
    rendered_aiff: Path,
    source_file: Path,
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
        "TIT2": [final_title],
        "TPE1": [artist_value],
        "TPE2": [album_artist_value],
        "TCON": [genre_value],
        "TALB": [album_value],
        "COMM:essentia": [essentia_comment],
    }
    if year_value:
        result_tags["TDRC"] = [year_value]

    print("[START] Write tags to processed AIFF")
    if dry_run:
        print(f"[DRY-RUN] tags for {rendered_aiff.name}: {json.dumps(result_tags, ensure_ascii=False)}")
        print("[DONE] Write tags to processed AIFF")
        return result_tags

    artwork_frames = read_artwork_frames(source_file=source_file, rendered_aiff=rendered_aiff)

    audio = AIFF(rendered_aiff)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"Failed to initialize ID3 tags for {rendered_aiff}")
    tags.clear()

    tags.setall("TIT2", [TIT2(encoding=3, text=[final_title])])
    tags.setall("TPE1", [TPE1(encoding=3, text=[artist_value])])
    tags.setall("TPE2", [TPE2(encoding=3, text=[album_artist_value])])
    if year_value:
        tags.setall("TDRC", [TDRC(encoding=3, text=[year_value])])
    tags.setall("TCON", [TCON(encoding=3, text=[genre_value])])
    tags.setall("TALB", [TALB(encoding=3, text=[album_value])])
    tags.add(COMM(encoding=3, lang="eng", desc="", text=[essentia_comment]))
    tags.add(COMM(encoding=3, lang="eng", desc="essentia", text=[essentia_comment]))
    for frame in artwork_frames:
        tags.add(copy.deepcopy(frame))
    audio.save()

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
    default_genre: str,
    max_tracks: int | None,
    clean_start: bool,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    paths = build_prep_paths(prep_root)
    ensure_dirs(paths)
    if clean_start:
        clean_working_directories(paths, dry_run=dry_run)

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
        maybe_confirm(confirm_steps, "After copying template input.aiff")

        sync_reaper_project_to_input(
            reaper_project=resolved_reaper_project,
            input_audio=paths.templates / "input.aiff",
            output_audio=paths.templates / "output.aif",
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After syncing Reaper project")

        run_reaper_render(
            reaper_exe=reaper_exe,
            reaper_project=resolved_reaper_project,
            templates_dir=paths.templates,
            logs_dir=paths.logs,
            file_stem=source_file.stem,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After Reaper render")

        rendered_aiff = rename_render_output(
            templates_dir=paths.templates,
            processed_dir=paths.processed_aiff,
            target_stem=source_file.stem,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After renaming rendered output")

        essentia_json = run_essentia_single(
            rendered_file=rendered_aiff,
            logs_dir=paths.logs,
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
            source_file=source_file,
            source_tags=source_tags,
            metadata_entry=metadata_match.entry,
            essentia_comment=essentia_comment,
            default_genre=default_genre,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After writing processed AIFF tags")

        tagged_aiff = copy_processed_to_tagged(
            rendered_aiff,
            tagged_dir=paths.tagged_aiff,
            dry_run=dry_run,
        )
        maybe_confirm(confirm_steps, "After copying tagged AIFF")

        print(
            "[INFO] Audio processing summary: "
            f"converted='{converted_aiff.name}', rendered='{rendered_aiff.name}', tagged='{tagged_aiff.name}'"
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
                "template_input": str(paths.templates / "input.aiff"),
                "processed_aiff": str(rendered_aiff),
                "tagged_aiff": str(tagged_aiff),
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

    enrichment_by_track = build_essentia_enrichment(processed_records)
    for record in processed_records:
        track_key = str(record.get("processed_aiff", ""))
        enrichment = enrichment_by_track.get(track_key)
        if not enrichment:
            continue

        updated_comment = str(enrichment.get("comment", "no-summary"))
        record["essentia_comment"] = updated_comment
        record["essentia_analysis"] = enrichment

        processed_tags = record.get("processed_tags")
        if isinstance(processed_tags, dict):
            processed_tags["COMM:essentia"] = [updated_comment]

        processed_aiff_path = Path(str(record.get("processed_aiff", "")))
        tagged_aiff_path = Path(str(record.get("tagged_aiff", "")))

        if processed_aiff_path.exists() or dry_run:
            update_essentia_comment_frames(processed_aiff_path, updated_comment, dry_run=dry_run)
        if tagged_aiff_path.exists() or dry_run:
            update_essentia_comment_frames(tagged_aiff_path, updated_comment, dry_run=dry_run)

    write_processed_metadata(processed_records, paths.processed_metadata_file, dry_run=dry_run)
    print("\nFlow complete.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DJ set prep workflow on Sourcefiles.")
    parser.add_argument("--prep-root", type=Path, default=DEFAULT_PREP_ROOT, help="DJ-SET-PREP root directory.")
    parser.add_argument(
        "--set-file",
        type=Path,
        default=None,
        help="Optional metadata file path. Default: Metadata/raw-track-metadata.csv under prep root.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional source audio directory. Default: Sourcefiles under prep root.",
    )
    parser.add_argument("--ffmpeg-exe", default="ffmpeg")
    parser.add_argument("--reaper-exe", type=Path, default=DEFAULT_REAPER_EXE)
    parser.add_argument(
        "--reaper-project",
        type=Path,
        default=None,
        help="Optional Reaper project path. Default: Templates/DJ Set Prep.rpp under prep root.",
    )

    parser.add_argument("--default-genre", default="Electronic")
    parser.add_argument("--max-tracks", type=int, default=None)
    parser.add_argument(
        "--clean-start",
        action="store_true",
        help="Clear ConvertedFiles, ProcessedFiles, and TaggedFiles before processing.",
    )
    parser.add_argument("--confirm-steps", action="store_true", help="Pause for confirmation after each stage.")
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

        default_genre=args.default_genre,
        max_tracks=args.max_tracks,
        clean_start=args.clean_start,
        dry_run=args.dry_run,
        confirm_steps=args.confirm_steps,
    )


if __name__ == "__main__":
    main()
