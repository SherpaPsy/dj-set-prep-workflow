from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TPE2, TDRC


SEPARATOR = "===================="

# Global configuration
YEAR = 2026
DJ_SET_PREP_ROOT = Path(r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep")
AIFF_SOURCE = DJ_SET_PREP_ROOT / "Sourcefiles"
INIT_TARGET_PATH = DJ_SET_PREP_ROOT / "Metadata"


@dataclass(slots=True)
class TrackEntry:
    title: str
    artist: str
    label: str | None
    year: str | None


def parse_set_file(set_file: Path) -> list[TrackEntry]:
    if not set_file.exists() or set_file.stat().st_size == 0:
        raise ValueError(f"Set file is empty or missing content: {set_file}")

    raw_lines = [line.strip() for line in set_file.read_text(encoding="utf-8").splitlines()]
    lines = [
        line
        for line in raw_lines
        if line and line != SEPARATOR and not re.fullmatch(r"={8,}", line)
    ]

    if len(lines) % 3 != 0:
        raise ValueError(
            "Set file should have title/artist/[label year] triplets. "
            f"Found {len(lines)} non-empty content lines."
        )

    tracks: list[TrackEntry] = []
    for idx in range(0, len(lines), 3):
        title = lines[idx]
        artist = lines[idx + 1]
        label_line = lines[idx + 2]

        label, year = parse_label_year(label_line)
        tracks.append(TrackEntry(title=title, artist=artist, label=label, year=year))

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


def find_set_file(set_dir: Path, explicit_set_file: Path | None) -> Path:
    if explicit_set_file:
        return explicit_set_file

    txt_files = sorted(set_dir.glob("*.txt"))
    if not txt_files:
        raise FileNotFoundError(f"No .txt file found in {set_dir}")

    raw_candidates = [path for path in txt_files if "raw" in path.stem.lower()]
    non_empty_raw = [path for path in raw_candidates if path.stat().st_size > 0]
    if non_empty_raw:
        return non_empty_raw[0]

    non_empty_txt = [path for path in txt_files if path.stat().st_size > 0]
    if non_empty_txt:
        return non_empty_txt[0]

    return raw_candidates[0] if raw_candidates else txt_files[0]


def find_aiff_files(source_dir: Path) -> list[Path]:
    return sorted(
        [
            *source_dir.rglob("*.aif"),
            *source_dir.rglob("*.aiff"),
        ]
    )


def best_match_aiff(entry: TrackEntry, aiff_files: list[Path], used: set[Path]) -> Path | None:
    scored = score_candidate_aiffs(entry, aiff_files, used)
    if scored:
        return scored[0][1]
    return None


def score_candidate_aiffs(
    entry: TrackEntry,
    aiff_files: list[Path],
    used: set[Path],
) -> list[tuple[int, Path]]:
    title_key = normalize(entry.title)
    artist_key = normalize(entry.artist)

    scored: list[tuple[int, Path]] = []
    for path in aiff_files:
        if path in used:
            continue
        stem = normalize(path.stem)
        score = 0
        if title_key and title_key in stem:
            score += 2
        if artist_key and artist_key in stem:
            score += 1
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
    if top_score < 3:
        return True
    return False


def select_match_aiff(
    entry: TrackEntry,
    aiff_files: list[Path],
    used: set[Path],
    interactive_unsure: bool,
) -> Path | None:
    scored = score_candidate_aiffs(entry, aiff_files, used)
    if not scored:
        print(f"[NO MATCH] {entry.artist} - {entry.title}")
        return None

    top_score, top_path = scored[0]
    unsure = is_uncertain_match(scored)
    if not unsure:
        return top_path

    top_preview = ", ".join(path.name for _, path in scored[:3])
    print(
        f"[UNSURE] {entry.artist} - {entry.title} -> best score {top_score}; "
        f"candidates: {top_preview}"
    )

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


def tag_aiff(
    aiff_path: Path,
    entry: TrackEntry,
    default_genre: str,
    dry_run: bool,
) -> None:
    audio = AIFF(aiff_path)
    tags = audio.tags

    if tags is None:
        tags = ID3()
        audio.tags = tags

    existing_title = _first_text(tags, "TIT2")
    base_title = existing_title or entry.title.strip() or aiff_path.stem

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

    artist_frame = tags.get("TPE1")
    if artist_frame and artist_frame.text:
        album_artist_value = artist_frame.text[0]
    else:
        album_artist_value = entry.artist
        if not artist_frame:
            tags.setall("TPE1", [TPE1(encoding=3, text=[entry.artist])])

    tags.setall("TPE2", [TPE2(encoding=3, text=[album_artist_value])])

    if entry.year and not tags.get("TDRC"):
        tags.setall("TDRC", [TDRC(encoding=3, text=[entry.year])])

    if not tags.get("TCON"):
        tags.setall("TCON", [TCON(encoding=3, text=[default_genre])])

    if not tags.get("TALB"):
        tags.setall("TALB", [TALB(encoding=3, text=["DJ Set Prep"])])

    if dry_run:
        print(f"[DRY-RUN] {aiff_path.name} -> title='{final_title}', artist='{entry.artist}'")
        return

    audio.save()
    print(f"[TAGGED] {aiff_path.name}")


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
    aiff_files = find_aiff_files(source_dir)

    if not aiff_files:
        raise FileNotFoundError(f"No .aif/.aiff files found in source dir: {source_dir}")

    print(f"Using set file: {resolved_set_file}")
    print(f"Using source AIFF dir: {source_dir}")
    print(f"Parsed tracks: {len(entries)}")
    print(f"AIFF files found: {len(aiff_files)}")

    used_paths: set[Path] = set()
    unmatched_entries: list[TrackEntry] = []

    for entry in entries:
        match = select_match_aiff(
            entry,
            aiff_files,
            used_paths,
            interactive_unsure=interactive_unsure,
        )
        if not match:
            unmatched_entries.append(entry)
            continue

        used_paths.add(match)
        tag_aiff(match, entry, default_genre=default_genre, dry_run=dry_run)

    if unmatched_entries:
        print("\nUnmatched entries:")
        for entry in unmatched_entries:
            print(f"- {entry.artist} - {entry.title}")

    leftover_aiffs = [path for path in aiff_files if path not in used_paths]
    if leftover_aiffs:
        print("\nAIFF files not matched to set entries:")
        for path in leftover_aiffs:
            print(f"- {path.name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tag DJ set AIFF files using a set text file.")
    parser.add_argument("set_dir", type=Path, help="Folder containing the set .txt file and AIFF files.")
    parser.add_argument(
        "--set-file",
        type=Path,
        default=None,
        help="Optional explicit path to set text file. Defaults to *raw*.txt or first .txt in set_dir.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=AIFF_SOURCE,
        help=(
            "Root folder containing source AIFF files (searched recursively). "
            "Default: C:\\Users\\sherp\\OneDrive\\Music\\DJ-Set-Prep\\Sourcefiles"
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