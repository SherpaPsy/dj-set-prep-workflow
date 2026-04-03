#!/usr/bin/env python3
"""Generate a CSV report from processed metadata JSONL file."""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dj_set_prep_workflow.paths import resolve_default_prep_root


DEFAULT_PREP_ROOT = resolve_default_prep_root()


def resolve_default_metadata_file(prep_root: Path) -> Path:
    metadata_dir = prep_root / "Metadata"
    candidates = [
        metadata_dir / "processed-track-metadata.txt",
        metadata_dir / "processed_metadata.jsonl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def extract_tag_value(tags_dict: dict[str, Any], tag_key: str) -> str:
    """Extract a single value from a tags dictionary.
    
    Args:
        tags_dict: Dictionary mapping tag keys to lists of values
        tag_key: The ID3 tag key to extract (e.g., 'TIT2', 'TPE1')
    
    Returns:
        The first value if it exists, otherwise empty string
    """
    if not tags_dict:
        return ""
    values = tags_dict.get(tag_key, [])
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


def generate_report(metadata_file: Path, output_csv: Path) -> None:
    """Generate a CSV report from JSONL metadata file.
    
    Args:
        metadata_file: Path to the JSONL metadata file
        output_csv: Path where the CSV report should be written
    """
    if not metadata_file.exists():
        print(f"Error: Metadata file not found: {metadata_file}")
        default_candidate = resolve_default_metadata_file(DEFAULT_PREP_ROOT)
        if metadata_file != default_candidate:
            print(f"Default workflow location: {default_candidate}")
        return

    records = []
    
    # Read JSONL file
    with open(metadata_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    print(f"Warning: Failed to parse JSON line: {e}")
    
    if not records:
        print("No records found in metadata file")
        return
    
    # Generate CSV
    fieldnames = [
        "Source File",
        "Title",
        "Artist",
        "Album Artist",
        "Genre",
        "Album",
        "Year",
        "Essentia Comment",
        "Metadata Source",
        "Processed AIFF",
        "Essentia JSON",
    ]
    
    with open(output_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for record in records:
            source = record.get("source", {})
            processed_tags = record.get("processed_tags", {})
            
            row = {
                "Source File": source.get("file_name", ""),
                "Title": extract_tag_value(processed_tags, "TIT2"),
                "Artist": extract_tag_value(processed_tags, "TPE1"),
                "Album Artist": extract_tag_value(processed_tags, "TPE2"),
                "Genre": extract_tag_value(processed_tags, "TCON"),
                "Album": extract_tag_value(processed_tags, "TALB"),
                "Year": extract_tag_value(processed_tags, "TDRC"),
                "Essentia Comment": record.get("essentia_comment", ""),
                "Metadata Source": record.get("metadata_match_source", ""),
                "Processed AIFF": Path(record.get("tagged_aiff", "")).name,
                "Essentia JSON": Path(record.get("essentia_json", "")).name,
            }
            writer.writerow(row)
    
    print(f"Report generated: {output_csv}")
    print(f"Total records: {len(records)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a CSV report from DJ Set Prep processed metadata."
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="Path to the processed metadata JSONL file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV file path. Default: metadata_report.csv",
    )
    
    args = parser.parse_args()
    
    # Determine metadata file path
    if args.metadata_file:
        metadata_file = args.metadata_file
    else:
        metadata_file = resolve_default_metadata_file(DEFAULT_PREP_ROOT)
    
    # Determine output path
    if args.output:
        output_csv = args.output
    else:
        output_csv = metadata_file.parent / "metadata_report.csv"
    
    generate_report(metadata_file, output_csv)


if __name__ == "__main__":
    main()
