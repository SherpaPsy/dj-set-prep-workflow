from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from mutagen._riff import RiffFile


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_artist_title(stem: str) -> tuple[str, str]:
    parts = stem.split("-")
    if len(parts) < 2:
        raise ValueError(f"Filename does not match expected pattern: {stem}")

    artist_part = parts[0]
    title_parts = parts[1:]

    if parts[-1].isdigit():
        title_parts = parts[1:-1]

    artist = _normalize_spaces(artist_part.replace("_", " "))
    title_raw = _normalize_spaces(" ".join(title_parts).replace("_", " "))

    words = title_raw.split()
    if len(words) >= 2 and words[-1].lower() == "mix":
        title = _normalize_spaces(" ".join(words[:-2]) + f" ({' '.join(words[-2:])})")
    else:
        title = title_raw

    return artist, title


def _riff_info_data(value: str) -> bytes:
    return (value + "\x00").encode("utf-8")


def _find_or_create_info_list(riff: RiffFile):
    for chunk in riff.root.subchunks():
        if chunk.id == "LIST" and getattr(chunk, "name", None) == "INFO":
            return chunk
    return riff.insert_chunk("LIST", data=b"INFO")


def _write_list_info_tags(target_path: Path, artist: str, title: str) -> None:
    with target_path.open("r+b") as fileobj:
        riff = RiffFile(fileobj)
        info_list = _find_or_create_info_list(riff)

        for key in ("IART", "INAM"):
            if key in info_list:
                del info_list[key]

        info_list.insert_chunk("IART", data=_riff_info_data(artist))
        info_list.insert_chunk("INAM", data=_riff_info_data(title))


def _default_output_name(source_path: Path) -> Path:
    return source_path.with_name(f"{source_path.stem}-Tagged-list-info{source_path.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tag a WAV copy using RIFF LIST/INFO tags derived from filename."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(
            r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\ProcessedWAV\Azee_Project-"
            r"Raise-Main_Mix-78594226.wav"
        ),
        help="Source WAV file to copy and tag.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output WAV file to create (default: <source>-Tagged-list-info.wav).",
    )
    args = parser.parse_args()

    source_path = args.source
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    output_path = args.output or _default_output_name(source_path)

    artist, title = _parse_artist_title(source_path.stem)
    shutil.copy2(source_path, output_path)
    _write_list_info_tags(output_path, artist=artist, title=title)

    print(f"Created: {output_path}")
    print(f"Artist: {artist}")
    print(f"Title: {title}")


if __name__ == "__main__":
    main()
