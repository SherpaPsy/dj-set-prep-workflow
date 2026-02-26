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
from mutagen.aiff import AIFF
from mutagen.id3 import APIC, ID3, COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2

from .tag_set_mp3s import TrackEntry, normalize, parse_set_file

DEFAULT_PREP_ROOT = Path(r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep")
DEFAULT_TEMPLATES_ROOT = Path(r"C:\Code\Personal\dj-set-prep-workflow\Templates")
DEFAULT_REAPER_EXE = Path(r"C:\Program Files\REAPER (x64)\reaper.exe")
DEFAULT_FFMPEG_EXE = Path(r"D:\AudioTools\ffmpeg\bin\ffmpeg.exe")
DEFAULT_ESSENTIA_EXE = Path(
    r"D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}


@dataclass(slots=True)
class PrepPaths:
    root: Path
    artwork: Path
    coverart: Path
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


@dataclass(slots=True)
class TrackProcessingData:
    """Tracks all data for a single file through all processing stages."""
    source_file: Path
    source_tags: dict[str, Any] | None = None
    cover_art_file: Path | None = None
    converted_aiff: Path | None = None
    rendered_aiff: Path | None = None
    tagged_aiff: Path | None = None
    essentia_json: Path | None = None
    essentia_comment: str | None = None
    metadata_match: MetadataMatch | None = None
    processed_tags: dict[str, list[str]] | None = None


def build_prep_paths(prep_root: Path, templates_root: Path) -> PrepPaths:
    metadata_dir = prep_root / "Metadata"
    return PrepPaths(
        root=prep_root,
        artwork=prep_root / "Artwork",
        coverart=prep_root / "Coverart",
        converted_aiff=prep_root / "ConvertedFiles",
        logs=prep_root / "Logs",
        metadata=metadata_dir,
        processed_aiff=prep_root / "ProcessedFiles",
        tagged_aiff=prep_root / "TaggedFiles",
        source_files=prep_root / "SourceFiles",
        templates=templates_root,
        raw_metadata_file=metadata_dir / "raw-track-metadata.txt",
        processed_metadata_file=metadata_dir / "processed-track-metadata.txt",
    )


def ensure_dirs(paths: PrepPaths) -> None:
    for directory in [
        paths.root,
        paths.artwork,
        paths.coverart,
        paths.converted_aiff,
        paths.logs,
        paths.metadata,
        paths.processed_aiff,
        paths.tagged_aiff,
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


def extract_cover_art(audio_path: Path, coverart_dir: Path, dry_run: bool) -> Path | None:
    """Extract cover art from audio file and save it to the coverart directory."""
    print(f"[START] Extract cover art from {audio_path.name}")
    
    try:
        audio = MutagenFile(str(audio_path))
        if audio is None:
            print("[INFO] No audio metadata found, skipping cover art extraction")
            print("[DONE] Extract cover art")
            return None
        
        cover_data = None
        
        # Try to extract cover art from ID3 tags (MP3)
        if hasattr(audio, "tags") and audio.tags is not None:
            if hasattr(audio.tags, "getall"):
                # ID3 tags
                for frame in audio.tags.getall("APIC"):
                    if frame.type == 3 or (hasattr(frame, "type") and frame.type == 3):  # Front cover
                        cover_data = frame.data
                        break
                # If no front cover, get the first APIC frame
                if not cover_data:
                    apic_frames = audio.tags.getall("APIC")
                    if apic_frames:
                        cover_data = apic_frames[0].data
        
        # Try MP4 style metadata
        if not cover_data and hasattr(audio, "tags") and audio.tags is not None:
            if "covr" in audio.tags:
                cover_data = audio.tags["covr"][0] if audio.tags["covr"] else None
        
        if not cover_data:
            print("[INFO] No cover art found in audio metadata")
            print("[DONE] Extract cover art")
            return None
        
        # Save cover art
        cover_output = coverart_dir / f"{audio_path.stem}.jpg"
        
        if dry_run:
            print(f"[DRY-RUN] Would save cover art to: {cover_output}")
        else:
            cover_output.write_bytes(cover_data)
            print(f"[INFO] Cover art saved: {cover_output.name}")
        
        print("[DONE] Extract cover art")
        return cover_output
    
    except Exception as e:
        print(f"[WARNING] Error extracting cover art: {e}")
        print("[DONE] Extract cover art")
        return None


def embed_cover_art(tagged_aiff: Path, coverart_file: Path, dry_run: bool) -> Path | None:
    """Embed cover art into AIFF file using mutagen."""
    if not coverart_file or not coverart_file.exists():
        print("[INFO] Cover art file not found or None, skipping embedding")
        return None
    
    print(f"[START] Embed cover art in {tagged_aiff.name}")
    
    try:
        # Read cover art data
        cover_data = coverart_file.read_bytes()
        
        if dry_run:
            print(f"[DRY-RUN] Would embed {len(cover_data)} bytes of cover art into {tagged_aiff.name}")
            print("[DONE] Embed cover art")
            return None
        
        # Open AIFF file and add cover art to ID3 tags
        audio = AIFF(str(tagged_aiff))
        if audio.tags is None:
            audio.add_tags()
        
        tags = audio.tags
        
        # Remove any existing picture frames
        for key in list(tags.keys()):
            if key.startswith("APIC"):
                del tags[key]
        
        # Add the new picture frame
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=cover_data))
        
        audio.save()
        
        print(f"[INFO] Cover art embedded successfully in: {tagged_aiff.name}")
        print("[DONE] Embed cover art")
        return tagged_aiff
    
    except Exception as e:
        print(f"[ERROR] Error embedding cover art: {e}")
        print("[DONE] Embed cover art")
        return None


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


def convert_to_wav(source_file: Path, converted_dir: Path, ffmpeg_exe: str, dry_run: bool) -> Path:
    resolved_ffmpeg = shutil.which(ffmpeg_exe) if ffmpeg_exe else None
    if not resolved_ffmpeg:
        raise FileNotFoundError(
            "ffmpeg executable not found. Provide --ffmpeg-exe with a full path or add ffmpeg to PATH."
        )
    output_path = converted_dir / f"{source_file.stem}.wav"
    cmd = [resolved_ffmpeg, "-y", "-i", str(source_file), "-c:a", "pcm_s24le", str(output_path)]
    print(f"[START] Convert -> {output_path.name}")
    if dry_run:
        print(f"[DRY-RUN] ffmpeg: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    print(f"[INFO] Converted WAV: {output_path}")
    print("[DONE] Convert")
    return output_path


def convert_wav_to_aiff(wav_file: Path, aiff_dir: Path, ffmpeg_exe: str, dry_run: bool) -> Path:
    """Convert WAV file to AIFF format."""
    resolved_ffmpeg = shutil.which(ffmpeg_exe) if ffmpeg_exe else None
    if not resolved_ffmpeg:
        raise FileNotFoundError(
            "ffmpeg executable not found. Provide --ffmpeg-exe with a full path or add ffmpeg to PATH."
        )
    output_path = aiff_dir / f"{wav_file.stem}.aif"
    cmd = [resolved_ffmpeg, "-y", "-i", str(wav_file), "-c:a", "pcm_s24be", str(output_path)]
    print(f"[START] Convert WAV to AIFF -> {output_path.name}")
    if dry_run:
        print(f"[DRY-RUN] ffmpeg: {' '.join(cmd)}")
    else:
        subprocess.run(cmd, check=True)
    print(f"[INFO] Converted to AIFF: {output_path}")
    print("[DONE] Convert WAV to AIFF")
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
    
    # Delete old output file to ensure fresh render
    if output_path.exists():
        if dry_run:
            print(f"[DRY-RUN] delete old output: {output_path}")
        else:
            output_path.unlink()
            print(f"[INFO] Deleted old render output: {output_path}")
    
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


def move_render_output(templates_dir: Path, processed_dir: Path, target_stem: str, dry_run: bool) -> Path:
    src = templates_dir / "output.aif"
    dst = processed_dir / f"{target_stem}.aif"
    print(f"[START] Move render output -> {dst.name}")
    if dry_run:
        print(f"[DRY-RUN] move: {src} -> {dst}")
    else:
        if not src.exists():
            raise FileNotFoundError(f"Expected Reaper output not found: {src}")
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
    print(f"[INFO] Rendered AIFF: {dst}")
    print("[DONE] Move render output")
    return dst


def build_batch_convert_file(
    converted_aiffs: list[Path],
    template_file: Path,
    output_file: Path,
    dry_run: bool,
) -> Path:
    """Build a Reaper batch convert file by prepending input files to the template."""
    print("[START] Build batch convert file")
    
    if not template_file.exists():
        raise FileNotFoundError(f"Batch convert template not found: {template_file}")
    
    print(f"[INFO] Batch template path: {template_file}")

    # Read template content
    template_content = template_file.read_text(encoding="utf-8")

    template_fxchain = next(
        (line for line in template_content.splitlines() if line.startswith("FXCHAIN ")),
        None,
    )
    if template_fxchain:
        print(f"[INFO] Template FXCHAIN: {template_fxchain}")
    if "DJ-Pre-Master.RfxChain" not in template_content:
        raise RuntimeError(
            "Template does not contain expected FXCHAIN line 'DJ-Pre-Master.RfxChain'. "
            "Save the template file and re-run."
        )
    
    print(f"[INFO] Template content length: {len(template_content)} bytes")
    
    # Build the file list (full paths, one per line)
    file_list_lines = [str(aiff) for aiff in converted_aiffs]
    file_list = "\n".join(file_list_lines)
    
    # Assemble final content: files + newline + template (unchanged)
    final_content = file_list + ("\n" if file_list else "") + template_content
    
    if dry_run:
        print(f"[DRY-RUN] Would write batch convert file to: {output_file}")
        print(f"[DRY-RUN] Files to process: {len(converted_aiffs)}")
        for aiff in converted_aiffs:
            print(f"[DRY-RUN]   - {aiff}")
        print(f"[DRY-RUN] Content preview (first 500 chars):\n{final_content[:500]}")
        print(f"[DRY-RUN] Content length: {len(final_content)} bytes")
    else:
        # Always regenerate the batch file
        if output_file.exists():
            output_file.unlink()
        output_file.write_text(final_content, encoding="utf-8")
        
        print(f"[INFO] Batch convert file created: {output_file}")
        print(f"[INFO] Files to process: {len(converted_aiffs)}")
        for aiff in converted_aiffs:
            print(f"[INFO]   - {aiff}")
        print(f"[INFO] File size: {output_file.stat().st_size} bytes")
        
        # Verify what we wrote
        verify_content = output_file.read_text(encoding="utf-8")
        if not verify_content.endswith(template_content):
            raise RuntimeError(
                "batchconvert.txt config block does not match the template file. "
                "The template content must be copied verbatim after the file list."
            )
    
    print("[DONE] Build batch convert file")
    return output_file


def run_reaper_batch_convert(
    reaper_exe: Path,
    batch_file: Path,
    logs_dir: Path,
    processed_dir: Path,
    templates_dir: Path,
    dry_run: bool,
) -> None:
    """Run Reaper batch convert on all files and validate results."""
    log_path = templates_dir / "batchconvert.txt.log"
    cmd = [str(reaper_exe), "-batchconvert", str(batch_file)]
    
    print("[START] Reaper batch convert")
    print(f"[INFO] Batch file: {batch_file}")
    
    if dry_run:
        print(f"[DRY-RUN] Reaper: {' '.join(cmd)}")
    else:
        print("[INFO] Reaper batch processing started (this step can be slow)...")
        started = time.monotonic()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        while process.poll() is None:
            elapsed = int(time.monotonic() - started)
            print(f"[INFO] Reaper batch convert still running... {elapsed}s")
            time.sleep(5)
        
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd, output=stdout, stderr=stderr)
        
        # Check log for errors
        if log_path.exists():
            log_content = log_path.read_text(encoding="utf-8")
            print(f"[INFO] Batch convert log contents:\n{log_content}")
            if "FAIL" in log_content or "Can't open" in log_content or "ERROR" in log_content:
                print("[ERROR] Batch convert failed! Log contents:")
                print(log_content)
                raise RuntimeError("Reaper batch convert reported errors. Check log for details.")
        else:
            raise FileNotFoundError(f"Expected Reaper batch log not found: {log_path}")
        
        elapsed = time.monotonic() - started
        print(f"[INFO] Reaper batch convert duration: {elapsed:.1f}s")
    
    print("[DONE] Reaper batch convert")


def copy_to_tagged(rendered_aiff: Path, tagged_dir: Path, dry_run: bool) -> Path:
    tagged_path = tagged_dir / rendered_aiff.name
    print(f"[START] Copy to tagged -> {tagged_path.name}")
    if dry_run:
        print(f"[DRY-RUN] copy: {rendered_aiff} -> {tagged_path}")
    else:
        shutil.copy2(rendered_aiff, tagged_path)
    print("[DONE] Copy to tagged")
    return tagged_path


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


def write_tags_to_tagged_aiff(
    tagged_aiff: Path,
    source_tags: dict[str, Any],
    metadata_entry: TrackEntry | None,
    essentia_comment: str,
    default_genre: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    base_title = str((source_tags.get("title") or [source_tags.get("file_stem", tagged_aiff.stem)])[0]).strip()
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

    print("[START] Write tags to tagged AIFF")
    if dry_run:
        print(f"[DRY-RUN] tags for {tagged_aiff.name}: {json.dumps(result_tags, ensure_ascii=False)}")
        print("[DONE] Write tags to tagged AIFF")
        return result_tags

    audio = AIFF(str(tagged_aiff))
    if audio.tags is None:
        audio.add_tags()

    tags = audio.tags
    tags.clear()
    tags.add(TIT2(encoding=3, text=final_title))
    tags.add(TPE1(encoding=3, text=artist_value))
    tags.add(TPE2(encoding=3, text=album_artist_value))
    tags.add(TALB(encoding=3, text=album_value))
    tags.add(TCON(encoding=3, text=genre_value))
    if year_value:
        tags.add(TDRC(encoding=3, text=year_value))
    if essentia_comment:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=essentia_comment))

    audio.save()

    print("[DONE] Write tags to tagged AIFF")
    return result_tags


def write_processed_metadata(records: list[dict[str, Any]], output_file: Path, dry_run: bool) -> None:
    print("[START] Write processed metadata file")
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else "")
    if dry_run:
        print(f"[DRY-RUN] would write {len(records)} records to {output_file}")
    else:
        output_file.write_text(content, encoding="utf-8")
    print("[DONE] Write processed metadata file")


def stage1_extract_all(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 1: Extract tags and cover art from all source files."""
    print("\n" + "=" * 80)
    print("STAGE 1: EXTRACT METADATA AND COVER ART")
    print("=" * 80)
    
    for idx, track in enumerate(tracks, start=1):
        print(f"\n[{idx}/{len(tracks)}] Extracting from {track.source_file.name}")
        
        track.source_tags = extract_tags_dict(track.source_file)
        print("[INFO] Extracted tags dictionary:")
        print(json.dumps(track.source_tags, ensure_ascii=False, indent=2))
        
        track.cover_art_file = extract_cover_art(track.source_file, paths.coverart, dry_run=dry_run)
    
    maybe_confirm(confirm_steps, "After Stage 1: Extract all metadata and cover art")


def stage2_convert_all(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    ffmpeg_exe: str,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 2: Convert all source files to WAV."""
    print("\n" + "=" * 80)
    print("STAGE 2: CONVERT ALL FILES TO WAV")
    print("=" * 80)
    
    for idx, track in enumerate(tracks, start=1):
        print(f"\n[{idx}/{len(tracks)}] Converting {track.source_file.name}")
        track.converted_aiff = convert_to_wav(
            track.source_file, 
            paths.converted_aiff, 
            ffmpeg_exe=ffmpeg_exe, 
            dry_run=dry_run
        )
    
    maybe_confirm(confirm_steps, "After Stage 2: Convert all to WAV")


def stage3_render_all(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    reaper_exe: Path,
    reaper_project: Path,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 3: Batch convert all WAV files through Reaper."""
    print("\n" + "=" * 80)
    print("STAGE 3: BATCH CONVERT ALL FILES THROUGH REAPER")
    print("=" * 80)
    
    # Gather all converted WAV files (only .wav files)
    converted_wavs = [track.converted_aiff for track in tracks if track.converted_aiff.suffix.lower() == ".wav"]
    
    if not converted_wavs:
        raise ValueError("No .wav files found in converted files")
    
    # Build batch convert file in Templates directory
    batch_template = paths.templates / "batchconvert_template.txt"
    batch_file = paths.templates / "batchconvert.txt"
    
    build_batch_convert_file(
        converted_aiffs=converted_wavs,
        template_file=batch_template,
        output_file=batch_file,
        dry_run=dry_run,
    )
    
    # Run batch convert
    run_reaper_batch_convert(
        reaper_exe=reaper_exe,
        batch_file=batch_file,
        logs_dir=paths.logs,
        processed_dir=paths.processed_aiff,
        templates_dir=paths.templates,
        dry_run=dry_run,
    )
    
    # Set the rendered_wav paths for each track (Reaper will output WAV)
    # Log validation in run_reaper_batch_convert handles error detection
    print("\n[INFO] Setting rendered WAV file paths for next stage...")
    for track in tracks:
        track.rendered_aiff = paths.processed_aiff / f"{track.source_file.stem}.wav"
        print(f"[INFO] Track will use: {track.rendered_aiff.name}")
    
    maybe_confirm(confirm_steps, "After Stage 3: Batch convert all through Reaper")


def stage3b_convert_to_aiff(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    ffmpeg_exe: str,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 3b: Convert WAV files from batch convert back to AIFF for analysis."""
    print("\n" + "=" * 80)
    print("STAGE 3B: CONVERT WAV TO AIFF FOR ANALYSIS")
    print("=" * 80)
    
    for idx, track in enumerate(tracks, 1):
        print(f"\n[{idx}/{len(tracks)}] Converting WAV to AIFF: {track.source_file.name}")
        
        # rendered_aiff currently holds the WAV file path from batch convert
        wav_file = track.rendered_aiff
        
        if not wav_file.exists():
            raise FileNotFoundError(f"Batch convert WAV not found: {wav_file}")
        
        # Convert to AIFF
        aiff_file = convert_wav_to_aiff(
            wav_file=wav_file,
            aiff_dir=paths.processed_aiff,
            ffmpeg_exe=ffmpeg_exe,
            dry_run=dry_run,
        )
        
        # Update track to point to AIFF for analysis stage
        track.rendered_aiff = aiff_file
    
    maybe_confirm(confirm_steps, "After Stage 3b: Convert WAV to AIFF for analysis")


def stage4_analyze_all(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    essentia_exe: Path,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 4: Analyze all rendered files with Essentia."""
    print("\n" + "=" * 80)
    print("STAGE 4: ANALYZE ALL FILES WITH ESSENTIA")
    print("=" * 80)
    
    for idx, track in enumerate(tracks, start=1):
        print(f"\n[{idx}/{len(tracks)}] Analyzing {track.rendered_aiff.name}")
        
        track.essentia_json = run_essentia_single(
            rendered_file=track.rendered_aiff,
            logs_dir=paths.logs,
            essentia_exe=essentia_exe,
            dry_run=dry_run,
        )
        
        track.essentia_comment = extract_essentia_summary(track.essentia_json)
        print(f"[INFO] Essentia comment: {track.essentia_comment}")
    
    maybe_confirm(confirm_steps, "After Stage 4: Analyze all with Essentia")


def stage5_tag_all(
    tracks: list[TrackProcessingData],
    paths: PrepPaths,
    metadata_entries: list[TrackEntry],
    default_genre: str,
    dry_run: bool,
    confirm_steps: bool,
) -> None:
    """Stage 5: Tag all files and embed cover art."""
    print("\n" + "=" * 80)
    print("STAGE 5: TAG ALL FILES AND EMBED COVER ART")
    print("=" * 80)
    
    used_entry_indices: set[int] = set()
    
    for idx, track in enumerate(tracks, start=1):
        print(f"\n[{idx}/{len(tracks)}] Tagging {track.rendered_aiff.name}")
        
        track.tagged_aiff = copy_to_tagged(track.rendered_aiff, paths.tagged_aiff, dry_run=dry_run)
        
        track.metadata_match = find_metadata_match(
            metadata_entries,
            source_tags=track.source_tags,
            used_entry_indices=used_entry_indices,
            fallback_index=idx - 1,
        )
        print(f"[INFO] Metadata match source: {track.metadata_match.source}")
        
        track.processed_tags = write_tags_to_tagged_aiff(
            track.tagged_aiff,
            source_tags=track.source_tags,
            metadata_entry=track.metadata_match.entry,
            essentia_comment=track.essentia_comment,
            default_genre=default_genre,
            dry_run=dry_run,
        )
        
        embed_cover_art(track.tagged_aiff, track.cover_art_file, dry_run=dry_run)
        
        print(
            "[INFO] Audio processing summary: "
            f"converted='{track.converted_aiff.name}', rendered='{track.rendered_aiff.name}', tagged='{track.tagged_aiff.name}'"
        )
        print(f"[INFO] essentia='{track.essentia_json.name}'")
    
    maybe_confirm(confirm_steps, "After Stage 5: Tag all and embed cover art")


def run_flow(
    prep_root: Path,
    templates_root: Path,
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
    paths = build_prep_paths(prep_root, templates_root)
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
    print(f"\nProcessing mode: STAGE-BASED (extract all → convert to WAV → batch render → convert to AIFF → analyze all → tag all)")


    # Initialize track data structures
    tracks = [TrackProcessingData(source_file=sf) for sf in source_files]

    # Stage 1: Extract metadata and cover art from all files
    stage1_extract_all(tracks, paths, dry_run=dry_run, confirm_steps=confirm_steps)

    # Stage 2: Convert all files to AIFF
    stage2_convert_all(tracks, paths, ffmpeg_exe=ffmpeg_exe, dry_run=dry_run, confirm_steps=confirm_steps)

    # Stage 3: Render all files through Reaper
    stage3_render_all(
        tracks, 
        paths, 
        reaper_exe=reaper_exe, 
        reaper_project=resolved_reaper_project, 
        dry_run=dry_run, 
        confirm_steps=confirm_steps
    )

    # Stage 3b: Convert WAV output from batch convert back to AIFF
    stage3b_convert_to_aiff(
        tracks,
        paths,
        ffmpeg_exe=ffmpeg_exe,
        dry_run=dry_run,
        confirm_steps=confirm_steps,
    )

    if stop_after_render:
        print("\n[INFO] Stop-after-render enabled. Skipping Essentia, tagging, and metadata output.")
        print("\nFlow complete.")
        return

    # Stage 4: Analyze all files with Essentia
    stage4_analyze_all(tracks, paths, essentia_exe=essentia_exe, dry_run=dry_run, confirm_steps=confirm_steps)

    # Stage 5: Tag all files and embed cover art
    stage5_tag_all(
        tracks, 
        paths, 
        metadata_entries=metadata_entries, 
        default_genre=default_genre, 
        dry_run=dry_run, 
        confirm_steps=confirm_steps
    )

    # Build processed records for output
    processed_records: list[dict[str, Any]] = []
    for track in tracks:
        processed_records.append(
            {
                "source": {
                    "full_path": str(track.source_file),
                    "file_name": track.source_file.name,
                    "file_stem": track.source_file.stem,
                },
                "converted_aiff": str(track.converted_aiff),
                "template_input": str(paths.templates / "input.aif"),
                "processed_aiff": str(track.rendered_aiff),
                "tagged_aiff": str(track.tagged_aiff),
                "cover_art_file": str(track.cover_art_file) if track.cover_art_file else None,
                "essentia_json": str(track.essentia_json),
                "metadata_match_source": track.metadata_match.source if track.metadata_match else "none",
                "metadata_entry": {
                    "title": track.metadata_match.entry.title if track.metadata_match and track.metadata_match.entry else None,
                    "artist": track.metadata_match.entry.artist if track.metadata_match and track.metadata_match.entry else None,
                    "label": track.metadata_match.entry.label if track.metadata_match and track.metadata_match.entry else None,
                    "year": track.metadata_match.entry.year if track.metadata_match and track.metadata_match.entry else None,
                },
                "source_tags": track.source_tags,
                "processed_tags": track.processed_tags,
                "essentia_comment": track.essentia_comment,
            }
        )

    write_processed_metadata(processed_records, paths.processed_metadata_file, dry_run=dry_run)
    
    print("\n" + "=" * 80)
    print("ALL STAGES COMPLETE")
    print("=" * 80)
    print(f"Total tracks processed: {len(tracks)}")
    print(f"Processed metadata file: {paths.processed_metadata_file}")
    print("\nFlow complete.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DJ set prep workflow on Sourcefiles.")
    parser.add_argument("--prep-root", type=Path, default=DEFAULT_PREP_ROOT, help="DJ-SET-PREP root directory.")
    parser.add_argument("--templates-root", type=Path, default=DEFAULT_TEMPLATES_ROOT, help="Templates directory path.")
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
    parser.add_argument("--ffmpeg-exe", default=str(DEFAULT_FFMPEG_EXE))
    parser.add_argument("--reaper-exe", type=Path, default=DEFAULT_REAPER_EXE)
    parser.add_argument(
        "--reaper-project",
        type=Path,
        default=None,
        help="Optional Reaper project path. Default: Templates/DJ Set Prep.rpp under templates root.",
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
        templates_root=args.templates_root,
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
