from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import COMM, ID3, TALB, TCON, TIT2, TPE1, TPE2, TDRC
from mutagen.mp3 import MP3

from .paths import resolve_default_prep_root


SEPARATOR = "===================="
NOISE_TOKENS = {"ep"}

# Global configuration
YEAR = 2026

DJ_SET_PREP_ROOT = resolve_default_prep_root()

MP3_SOURCE = DJ_SET_PREP_ROOT / "SourceFiles"
INIT_TARGET_PATH = DJ_SET_PREP_ROOT / "Metadata"


@dataclass(slots=True)
class TrackEntry:
    title: str
    artist: str
    label: str | None
    year: str | None
    filename: str | None = None


def parse_set_file(set_file: Path) -> list[TrackEntry]:
    if not set_file.exists() or set_file.stat().st_size == 0:
        raise ValueError(f"Set file is empty or missing content: {set_file}")

    raw_lines = [line.strip() for line in set_file.read_text(encoding="utf-8").splitlines()]
    lines = [
        line
        for line in raw_lines
        if line and line != SEPARATOR and not re.fullmatch(r"={8,}", line)
    ]

    tracks: list[TrackEntry] = []
    for line_no, line in enumerate(lines, start=1):
        parts = [part.strip() for part in line.split("|", maxsplit=2)]
        if len(parts) != 3:
            raise ValueError(
                "Set file rows must be pipe-delimited as "
                "artist|title [label year]|filename. "
                f"Malformed row {line_no}: {line}"
            )

        artist, title_with_suffix, filename = parts
        if not artist or not title_with_suffix or not filename:
            raise ValueError(
                "Set file rows must provide artist, title, and filename. "
                f"Malformed row {line_no}: {line}"
            )

        title = title_with_suffix
        label = None
        year = None
        bracketed_tail = re.search(r"\[(?P<inner>[^\]]+)\]\s*$", title_with_suffix)
        if bracketed_tail:
            title_prefix = title_with_suffix[: bracketed_tail.start()].strip()
            if title_prefix:
                title = title_prefix
            label, year = parse_label_year(bracketed_tail.group("inner"))

        tracks.append(TrackEntry(title=title, artist=artist, label=label, year=year, filename=filename))

    return tracks


def parse_label_year(label_line: str) -> tuple[str | None, str | None]:
    cleaned = label_line.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()

    match = re.search(r"(19|20)\d{2}$", cleaned)
    if match:
        year = match.group(0)
        label = cleaned[: match.start()].strip() or None
        return label, year

    return (cleaned or None), None


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token]


def _significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in tokenize(text)
        if token not in NOISE_TOKENS and not token.isdigit()
    }


def _coverage_score(expected: str, candidate: str) -> int:
    expected_tokens = _significant_tokens(expected)
    if not expected_tokens:
        return 0

    candidate_tokens = _significant_tokens(candidate)
    overlap = len(expected_tokens & candidate_tokens)
    coverage = overlap / len(expected_tokens)

    if coverage >= 1.0:
        return 8
    if coverage >= 0.75:
        return 6
    if coverage >= 0.5:
        return 4
    if coverage > 0:
        return 2
    return 0


def _text_match_score(expected: str, candidate: str, *, weight: int) -> int:
    expected_key = normalize(expected)
    candidate_key = normalize(candidate)
    score = 0

    if expected_key and expected_key in candidate_key:
        score += weight

    score += _coverage_score(expected, candidate)
    return score


def find_set_file(set_dir: Path, explicit_set_file: Path | None) -> Path:
    if explicit_set_file:
        return explicit_set_file

    metadata_files = sorted([*set_dir.glob("*.txt"), *set_dir.glob("*.csv")])
    if not metadata_files:
        raise FileNotFoundError(f"No .txt or .csv metadata file found in {set_dir}")

    raw_candidates = [path for path in metadata_files if "raw" in path.stem.lower()]
    non_empty_raw = [path for path in raw_candidates if path.stat().st_size > 0]
    if non_empty_raw:
        return non_empty_raw[0]

    non_empty_metadata = [path for path in metadata_files if path.stat().st_size > 0]
    if non_empty_metadata:
        return non_empty_metadata[0]

    return raw_candidates[0] if raw_candidates else metadata_files[0]


def find_mp3_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.rglob("*.mp3"))


def best_match_mp3(entry: TrackEntry, mp3_files: list[Path], used: set[Path]) -> Path | None:
    scored = score_candidate_mp3s(entry, mp3_files, used)
    if scored:
        return scored[0][1]
    return None


def score_candidate_mp3s(entry: TrackEntry, mp3_files: list[Path], used: set[Path]) -> list[tuple[int, Path]]:
    scored: list[tuple[int, Path]] = []
    for path in mp3_files:
        if path in used:
            continue

        score = 0
        if entry.filename:
            expected_stem = Path(entry.filename).stem
            expected_key = normalize(expected_stem)
            candidate_key = normalize(path.stem)
            if expected_key and expected_key == candidate_key:
                score += 40
            elif expected_key and (expected_key in candidate_key or candidate_key in expected_key):
                score += 24

        score += _text_match_score(entry.title, path.stem, weight=12)
        score += _text_match_score(entry.artist, path.stem, weight=8)
        if score > 0:
            scored.append((score, path))

    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    return scored


def is_uncertain_match(scored: list[tuple[int, Path]]) -> bool:
    if not scored:
        return True

    top_score = scored[0][0]
    top_ties = [item for item in scored if item[0] == top_score]
    if len(top_ties) > 1:
        return True
    if top_score < 14:
        return True
    return False


def select_match_mp3(
    entry: TrackEntry,
    mp3_files: list[Path],
    used: set[Path],
    interactive_unsure: bool,
) -> Path | None:
    scored = score_candidate_mp3s(entry, mp3_files, used)
    if not scored:
        print(f"[NO MATCH] {entry.artist} - {entry.title}")
        return None

    top_score, top_path = scored[0]
    unsure = is_uncertain_match(scored)
    if not unsure:
        return top_path

    top_preview = ", ".join(path.name for _, path in scored[:3])
    print(f"[UNSURE] {entry.artist} - {entry.title} -> best score {top_score}; candidates: {top_preview}")

    if not interactive_unsure:
        return top_path

    print("Choose match:")
    for idx, (score, candidate_path) in enumerate(scored[:5], start=1):
        print(f"  {idx}. {candidate_path.name} (score={score})")
    print("  0. Skip this entry")

    while True:
        choice = input("Selection [default 1]: ").strip()
        if not choice:
            return scored[0][1]
        if choice.isdigit():
            selected = int(choice)
            if selected == 0:
                return None
            if 1 <= selected <= min(5, len(scored)):
                return scored[selected - 1][1]
        print("Invalid selection. Enter a number shown above.")


def _first_text(tags: ID3, key: str) -> str | None:
    frame = tags.get(key)
    if not frame or not getattr(frame, "text", None):
        return None
    value = str(frame.text[0]).strip()
    return value or None


def tag_mp3(
    mp3_path: Path,
    entry: TrackEntry,
    default_genre: str,
    dry_run: bool,
) -> None:
    audio = MP3(mp3_path)
    tags = audio.tags

    if tags is None:
        tags = ID3()
        audio.tags = tags

    base_title = entry.title.strip() or _first_text(tags, "TIT2") or mp3_path.stem

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

    tags.setall("TIT2", [TIT2(encoding=3, text=[final_title])])

    tags.setall("TPE1", [TPE1(encoding=3, text=[entry.artist])])
    tags.setall("TPE2", [TPE2(encoding=3, text=[entry.artist])])

    if entry.year and not tags.get("TDRC"):
        tags.setall("TDRC", [TDRC(encoding=3, text=[entry.year])])

    tags.delall("COMM")

    if not tags.get("TCON"):
        tags.setall("TCON", [TCON(encoding=3, text=[default_genre])])

    if not tags.get("TALB"):
        tags.setall("TALB", [TALB(encoding=3, text=["DJ Set Prep"])])

    if dry_run:
        print(f"[DRY-RUN] {mp3_path.name} -> title='{final_title}', artist='{entry.artist}'")
        return

    tags.save(mp3_path)
    print(f"[TAGGED] {mp3_path.name}")


def run(
    set_dir: Path,
    set_file: Path | None,
    source_dir: Path,
    default_genre: str,
    interactive_unsure: bool,
    dry_run: bool,
) -> None:
    resolved_set_file = find_set_file(set_dir, set_file)
    entries = parse_set_file(resolved_set_file)
    mp3_files = find_mp3_files(source_dir)

    if not mp3_files:
        raise FileNotFoundError(f"No .mp3 files found in source dir: {source_dir}")

    print(f"Using set file: {resolved_set_file}")
    print(f"Using source MP3 dir: {source_dir}")
    print(f"Parsed tracks: {len(entries)}")
    print(f"MP3 files found: {len(mp3_files)}")

    used_paths: set[Path] = set()
    unmatched_entries: list[TrackEntry] = []

    for entry in entries:
        match = select_match_mp3(
            entry,
            mp3_files,
            used_paths,
            interactive_unsure=interactive_unsure,
        )
        if not match:
            unmatched_entries.append(entry)
            continue

        used_paths.add(match)
        tag_mp3(match, entry, default_genre=default_genre, dry_run=dry_run)

    if unmatched_entries:
        print("\nUnmatched entries:")
        for entry in unmatched_entries:
            print(f"- {entry.artist} - {entry.title}")

    leftover_mp3s = [path for path in mp3_files if path not in used_paths]
    if leftover_mp3s:
        print("\nMP3s not matched to set entries:")
        for path in leftover_mp3s:
            print(f"- {path.name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tag DJ set MP3 files using a metadata file.")
    parser.add_argument("set_dir", type=Path, help="Folder containing the set metadata file and MP3 files.")
    parser.add_argument(
        "--set-file",
        type=Path,
        default=None,
        help="Optional explicit metadata file path. Defaults to *raw*.txt/*raw*.csv or first .txt/.csv in set_dir.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=MP3_SOURCE,
        help=(
            "Root folder containing source MP3 files (searched recursively). "
            f"Default: {MP3_SOURCE}"
        ),
    )
    parser.add_argument(
        "--default-genre",
        default="Electronic",
        help="Genre to add when missing (default: Electronic).",
    )
    parser.add_argument(
        "--interactive-unsure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prompt to choose candidate file for uncertain matches (default: enabled).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing tags.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(
        set_dir=args.set_dir,
        set_file=args.set_file,
        source_dir=args.source_dir,
        default_genre=args.default_genre,
        interactive_unsure=args.interactive_unsure,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
