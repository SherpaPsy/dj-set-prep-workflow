"""Prepare SourceFiles from the ReleasePromo folder.

Steps:
1. Clear the SourceFiles folder.
2. Copy every audio file that supports full tagging (mp3/aiff/flac/m4a) from the
   promo folder into SourceFiles. WAV files (limited tag support) are converted
   to AIFF instead, carrying their tags and artwork across.
3. Match each file in SourceFiles to a row in Metadata/tracklist.csv and write
   that row's Title and Artist into the file's tags.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.aiff import AIFF
from mutagen.id3 import ID3, TIT2, TPE1, ID3NoHeaderError
from mutagen.wave import WAVE

from .paths import resolve_default_prep_root

DEFAULT_PROMO_DIR = Path.home() / "Library" / "CloudStorage" / "OneDrive-Personal" / "Music" / "ReleasePromo On OneDrive"

# Formats with full tag support: copied as-is.
COPY_EXTENSIONS = {".mp3", ".aif", ".aiff", ".flac", ".m4a"}
# Formats with limited tag support: converted to AIFF.
CONVERT_EXTENSIONS = {".wav"}
ID3_EXTENSIONS = {".mp3", ".aif", ".aiff", ".wav"}
# Warn loudly if fewer than this fraction of promo files match the tracklist.
LOW_MATCH_FRACTION = 0.5

# Matching thresholds (0..1). A file is matched only if both artist and title
# clear their minimums; anything under CONFIDENT_SCORE is flagged for review.
MIN_ARTIST_SCORE = 0.6
MIN_TITLE_SCORE = 0.5
CONFIDENT_SCORE = 0.85

# Trailing "[Label 2026]", or a malformed "Label 2026]" (missing "[") that
# doesn't reach back past the "(... Mix)" part of the title.
TRAILING_BRACKET_RE = re.compile(r"\s*(?:\[[^\[\]]*\]|[^\[\]()]*\])\s*$")
LABEL_SUFFIX_RE = re.compile(r"\[[^\[\]]*\]\s*$")
# Trailing "(Original Mix)", "(Nicholas Van Orton Remix)", "(Radio Edit)" etc.
MIX_SUFFIX_RE = re.compile(r"\s*\([^()]*\b(?:mix|remix|edit|dub|version|rework)\)\s*$", re.IGNORECASE)


@dataclass(slots=True)
class CsvTrack:
    title: str
    artist: str


@dataclass(slots=True)
class FileTags:
    title: str
    artist: str
    album: str


@dataclass(slots=True)
class Match:
    path: Path
    track: CsvTrack
    tags: FileTags
    score: float
    artist_score: float
    title_score: float


# --------------------------------------------------------------------------- #
# Step 1 + 2: clear and copy/convert
# --------------------------------------------------------------------------- #


def list_promo_files(promo_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return (audio files to import, other files skipped)."""
    audio: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(promo_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() in COPY_EXTENSIONS | CONVERT_EXTENSIONS:
            audio.append(path)
        else:
            skipped.append(path)
    return audio, skipped


def clear_source_dir(source_dir: Path) -> int:
    removed = 0
    source_dir.mkdir(parents=True, exist_ok=True)
    for path in source_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    return removed


def _pcm_codec_for_wav(wav_path: Path) -> str:
    """Pick a big-endian PCM codec matching the WAV's bit depth (no truncation)."""
    try:
        bits = WAVE(wav_path).info.bits_per_sample
    except Exception:
        bits = 24
    if bits <= 16:
        return "pcm_s16be"
    if bits <= 24:
        return "pcm_s24be"
    return "pcm_s32be"


def convert_wav_to_aiff(wav_path: Path, dest_dir: Path, ffmpeg_exe: str) -> Path:
    output_path = dest_dir / f"{wav_path.stem}.aiff"
    cmd = [
        ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav_path),
        "-map", "0:a",
        "-map_metadata", "0",
        "-c:a", _pcm_codec_for_wav(wav_path),
        "-write_id3v2", "1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)

    # ffmpeg carries text tags but not artwork; copy any APIC frames across.
    try:
        wav_tags = WAVE(wav_path).tags
    except Exception:
        wav_tags = None
    if wav_tags is not None and wav_tags.getall("APIC"):
        aiff = AIFF(output_path)
        if aiff.tags is None:
            aiff.add_tags()
        for frame in wav_tags.getall("APIC"):
            aiff.tags.add(frame)
        aiff.save()

    return output_path


def import_promo_files(files: list[Path], dest_dir: Path, ffmpeg_exe: str) -> list[Path]:
    imported: list[Path] = []
    for src in files:
        if src.suffix.lower() in CONVERT_EXTENSIONS:
            out = convert_wav_to_aiff(src, dest_dir, ffmpeg_exe)
            print(f"  [CONVERT] {src.name} -> {out.name}")
        else:
            out = dest_dir / src.name
            shutil.copy2(src, out)
            print(f"  [COPY]    {src.name}")
        imported.append(out)
    return imported


# --------------------------------------------------------------------------- #
# Step 3: match against tracklist.csv and apply title/artist
# --------------------------------------------------------------------------- #


def load_tracklist(csv_path: Path) -> list[CsvTrack]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = {name.strip().lower(): name for name in (reader.fieldnames or [])}
        if "title" not in fields or "artist" not in fields:
            raise ValueError(f"{csv_path} must have 'Title' and 'Artist' columns (found: {reader.fieldnames})")
        tracks = []
        for row in reader:
            title = (row.get(fields["title"]) or "").strip()
            artist = (row.get(fields["artist"]) or "").strip()
            if title or artist:
                tracks.append(CsvTrack(title=title, artist=artist))
        return tracks


def _strip_label_suffix(title: str) -> str:
    """Drop a trailing '[Label 2026]' (or malformed 'Label 2026]')."""
    stripped = TRAILING_BRACKET_RE.sub("", title).strip()
    return stripped or title.strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _similarity(a: str, b: str) -> float:
    """Best of character-level ratio and token Dice coefficient."""
    norm_a = "".join(_tokens(a))
    norm_b = "".join(_tokens(b))
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    seq = SequenceMatcher(None, norm_a, norm_b).ratio()
    set_a, set_b = set(_tokens(a)), set(_tokens(b))
    dice = 2 * len(set_a & set_b) / (len(set_a) + len(set_b))
    return max(seq, dice)


def read_tags(path: Path) -> FileTags:
    ext = path.suffix.lower()
    if ext in ID3_EXTENSIONS:
        audio = MutagenFile(path)
        tags = getattr(audio, "tags", None) if audio is not None else None
        if tags is None:
            return FileTags("", "", "")

        def frame(key: str) -> str:
            return str(tags[key].text[0]) if key in tags and tags[key].text else ""

        return FileTags(frame("TIT2"), frame("TPE1"), frame("TALB"))

    audio = MutagenFile(path, easy=True)
    if audio is None or audio.tags is None:
        return FileTags("", "", "")

    def easy(key: str) -> str:
        return (audio.tags.get(key) or [""])[0]

    return FileTags(easy("title"), easy("artist"), easy("album"))


def read_title_artist(path: Path) -> tuple[str, str]:
    tags = read_tags(path)
    return tags.title, tags.artist


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _label_suffix(csv_title: str) -> str:
    """The trailing '[Label 2026]' from a CSV title, or '' if none."""
    match = LABEL_SUFFIX_RE.search(csv_title)
    return _clean_spaces(match.group(0)) if match else ""


def build_title(file_title: str, csv_title: str) -> str:
    """File's own track title (tidied, old bracket suffix dropped) + CSV '[Label Year]'.

    The CSV title comes from the pool web UI and is release-level (for EPs it
    names the EP, not the track), so only its label/year suffix is used.
    """
    base = _clean_spaces(_strip_label_suffix(file_title)) if file_title.strip() else ""
    if not base:
        return csv_title
    suffix = _label_suffix(csv_title)
    return f"{base} {suffix}".strip()


def _score_pair(src: FileTags, stem: str, track: CsvTrack) -> tuple[float, float, float]:
    csv_title = _strip_label_suffix(track.title)
    if src.title or src.artist:
        artist_score = _similarity(src.artist, track.artist)
        title_score = _similarity(_strip_label_suffix(src.title), csv_title)
        if src.album:
            # CSV title is often "<Release name> (<Mix>)": compare the release
            # part against the file's album tag.
            csv_release = MIX_SUFFIX_RE.sub("", csv_title).strip() or csv_title
            title_score = max(title_score, _similarity(src.album, csv_release))
    else:
        # No tags: fall back to the filename (e.g. Artist-Title-Mix-12345).
        stem_text = stem.replace("_", " ")
        artist_score = 1.0 if "".join(_tokens(track.artist)) in "".join(_tokens(stem_text)) else 0.0
        title_score = _similarity(stem_text, f"{track.artist} {csv_title}")
    return 0.4 * artist_score + 0.6 * title_score, artist_score, title_score


def match_files(files: list[Path], tracks: list[CsvTrack]) -> tuple[list[Match], list[Path], list[CsvTrack]]:
    """Globally best one-to-one assignment of files to CSV rows."""
    candidates: list[Match] = []
    for path in files:
        src = read_tags(path)
        for track in tracks:
            score, artist_score, title_score = _score_pair(src, path.stem, track)
            if artist_score >= MIN_ARTIST_SCORE and title_score >= MIN_TITLE_SCORE:
                candidates.append(Match(path, track, src, score, artist_score, title_score))

    candidates.sort(key=lambda m: m.score, reverse=True)
    matched: list[Match] = []
    used_files: set[Path] = set()
    used_tracks: set[int] = set()
    for cand in candidates:
        if cand.path in used_files or id(cand.track) in used_tracks:
            continue
        matched.append(cand)
        used_files.add(cand.path)
        used_tracks.add(id(cand.track))

    unmatched_files = [p for p in files if p not in used_files]
    unmatched_tracks = [t for t in tracks if id(t) not in used_tracks]
    return matched, unmatched_files, unmatched_tracks


def write_title_artist(path: Path, title: str, artist: str) -> None:
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.setall("TIT2", [TIT2(encoding=3, text=title)])
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
        tags.save(path)
        return

    if ext in {".aif", ".aiff"}:
        audio = AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.setall("TIT2", [TIT2(encoding=3, text=title)])
        audio.tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
        audio.save()
        return

    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported file for tagging: {path}")
    if audio.tags is None:
        audio.add_tags()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio.save()


def apply_tracklist(files: list[Path], tracklist_csv: Path) -> None:
    tracks = load_tracklist(tracklist_csv)
    print(f"  Tracklist rows: {len(tracks)}")
    matched, unmatched_files, unmatched_tracks = match_files(files, tracks)

    for m in sorted(matched, key=lambda m: m.path.name.lower()):
        flag = "" if m.score >= CONFIDENT_SCORE else "  <-- CHECK"
        title = build_title(m.tags.title, m.track.title)
        # Artist comes from the file (track-level); the CSV artist is release-level.
        artist = _clean_spaces(m.tags.artist) or m.track.artist
        print(f"  [TAG {m.score:.2f}] {m.path.name}{flag}")
        print(f"      title:  {title}")
        print(f"      artist: {artist}")
        if _strip_label_suffix(title) != _strip_label_suffix(m.track.title):
            print(f"      (tracklist row: {m.track.artist} | {m.track.title})")
        write_title_artist(m.path, title, artist)

    if unmatched_files:
        print(f"\n  [WARN] {len(unmatched_files)} file(s) had no match in the tracklist (tags left unchanged):")
        for path in unmatched_files:
            title, artist = read_title_artist(path)
            print(f"    - {path.name}  (artist={artist!r}, title={title!r})")
    if unmatched_tracks:
        print(f"\n  [WARN] {len(unmatched_tracks)} tracklist row(s) were not used:")
        for track in unmatched_tracks:
            print(f"    - {track.artist} | {track.title}")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def run_prepare_source_files(
    prep_root: Path,
    promo_dir: Path = DEFAULT_PROMO_DIR,
    tracklist_csv: Path | None = None,
    ffmpeg_exe: str = "ffmpeg",
    assume_yes: bool = False,
) -> None:
    source_dir = prep_root / "SourceFiles"
    tracklist_csv = tracklist_csv or prep_root / "Metadata" / "tracklist.csv"

    if not promo_dir.is_dir():
        print(f"[ERROR] Promo folder not found: {promo_dir}")
        return
    if not tracklist_csv.is_file():
        print(f"[ERROR] Tracklist not found: {tracklist_csv}")
        return

    promo_files, skipped = list_promo_files(promo_dir)
    if not promo_files:
        print(f"[ERROR] No audio files found in {promo_dir}; SourceFiles left untouched.")
        return

    # Preflight: match promo files against the tracklist before touching anything.
    tracks = load_tracklist(tracklist_csv)
    preflight_matched, preflight_unmatched, _ = match_files(promo_files, tracks)
    n_matched, n_total = len(preflight_matched), len(promo_files)
    if n_matched < n_total * LOW_MATCH_FRACTION:
        bar = "!" * 72
        print(f"\n{bar}")
        if n_matched == 0:
            print("!!  WARNING: NONE of the promo files match ANY row in the tracklist.")
        else:
            print(f"!!  WARNING: only {n_matched} of {n_total} promo files match the tracklist.")
        print("!!  The promo folder and tracklist.csv look like DIFFERENT sets.")
        print("!!  Did you forget to update ReleasePromo or tracklist.csv?")
        print(bar)
        print(f"  Promo files: {n_total}   Tracklist rows: {len(tracks)}   Matches: {n_matched}")
        print("  Unmatched promo files (first 5):")
        for path in preflight_unmatched[:5]:
            title, artist = read_title_artist(path)
            print(f"    - {artist or '?'} | {title or path.name}")
        print("  Tracklist rows (first 5):")
        for track in tracks[:5]:
            print(f"    - {track.artist} | {track.title}")
        print(bar)
        if assume_yes:
            print("Aborting (not overriding this check with --yes). SourceFiles left untouched.")
            return
        if input("\nType 'continue' to proceed anyway, anything else to abort: ").strip().lower() != "continue":
            print("Aborted. SourceFiles left untouched.")
            return
    else:
        print(f"Preflight: {n_matched} of {n_total} promo files match the tracklist.")

    existing = [p for p in source_dir.iterdir()] if source_dir.exists() else []
    print(f"Promo folder:  {promo_dir}  ({len(promo_files)} audio file(s))")
    print(f"SourceFiles:   {source_dir}  ({len(existing)} item(s) will be deleted)")
    print(f"Tracklist:     {tracklist_csv}")
    if not assume_yes:
        if input("\nClear SourceFiles and continue? [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return

    print("\n[STEP 1] Clear SourceFiles")
    print(f"  Removed {clear_source_dir(source_dir)} item(s)")

    print("\n[STEP 2] Copy / convert promo files")
    imported = import_promo_files(promo_files, source_dir, ffmpeg_exe)
    if skipped:
        print(f"  Skipped {len(skipped)} non-audio file(s): {', '.join(p.name for p in skipped)}")

    print("\n[STEP 3] Apply title/artist from tracklist")
    apply_tracklist(imported, tracklist_csv)

    print("\n[DONE] Prepare source files")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh SourceFiles from the promo folder and apply tracklist.csv titles/artists.")
    parser.add_argument("--prep-root", type=Path, default=resolve_default_prep_root())
    parser.add_argument("--promo-dir", type=Path, default=DEFAULT_PROMO_DIR)
    parser.add_argument("--tracklist", type=Path, default=None, help="Default: Metadata/tracklist.csv under prep root.")
    parser.add_argument("--ffmpeg-exe", default="ffmpeg")
    parser.add_argument("-y", "--yes", action="store_true", help="Don't ask before clearing SourceFiles.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_prepare_source_files(
        prep_root=args.prep_root,
        promo_dir=args.promo_dir,
        tracklist_csv=args.tracklist,
        ffmpeg_exe=args.ffmpeg_exe,
        assume_yes=args.yes,
    )


if __name__ == "__main__":
    main()
