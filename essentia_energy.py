import argparse
import csv
import json
from pathlib import Path
from typing import Any, Optional, cast

import numpy as np

try:
    from essentia.standard import MusicExtractor  # type: ignore[attr-defined]
except Exception:
    MusicExtractor = None


def flatten_essentia_features(essentia_output: dict) -> dict:
    """
    Flatten nested Essentia feature dict into dot-notation keys.
    
    E.g., {"rhythm": {"bpm": 120}} → {"rhythm.bpm": 120}
    Recursively flattens all levels.
    """
    flat = {}
    
    def recurse(d: dict, prefix: str = ""):
        for key, value in d.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                recurse(value, full_key)
            elif isinstance(value, (list, tuple)) and key.endswith(("mean", "stdev", "dmean2")):
                # For statistical keys, compute the statistic from array
                if isinstance(value, (list, tuple)):
                    if key.endswith("mean"):
                        flat[full_key] = float(np.mean(value)) if value else 0.0
                    elif key.endswith("stdev"):
                        flat[full_key] = float(np.std(value)) if value else 0.0
                    elif key.endswith("dmean2"):
                        # 2nd derivative approximation
                        if len(value) > 2:
                            diffs = np.diff(value)
                            d2 = np.diff(diffs)
                            flat[full_key] = float(np.mean(np.abs(d2))) if len(d2) > 0 else 0.0
                        else:
                            flat[full_key] = 0.0
            else:
                flat[full_key] = value
    
    recurse(essentia_output)
    return flat


def load_essentia_json(json_path: Path) -> Optional[dict]:
    """Load and flatten Essentia JSON output file."""
    try:
        with open(json_path) as f:
            data = json.load(f)
        # Essentia output is typically nested; flatten it
        return flatten_essentia_features(data)
    except Exception as e:
        print(f"  [WARN] Error loading {json_path.name}: {e}")
        return None


def essentia_features_to_dict(features: Any) -> dict:
    """Convert Essentia feature container to a plain JSON-serializable dict."""
    if hasattr(features, "items"):
        iterator = features.items()
    elif hasattr(features, "descriptorNames") and callable(features.descriptorNames):
        names = cast(list[str], features.descriptorNames())
        iterator = ((name, features[name]) for name in names)
    else:
        raise TypeError(f"Unsupported Essentia feature container: {type(features)!r}")

    output = {}
    for key, value in iterator:
        if hasattr(value, "tolist"):
            output[key] = value.tolist()
        elif isinstance(value, (int, float)):
            output[key] = float(value)
        else:
            output[key] = value
    return output


def extract_essentia_for_file(audio_path: Path) -> dict:
    """Run Essentia MusicExtractor for one audio file and return raw feature dict."""
    if MusicExtractor is None:
        raise ImportError("Essentia Python library not found. Install project dependencies first.")

    extractor = MusicExtractor()
    results = extractor(str(audio_path))

    if isinstance(results, tuple):
        features = results[0] if results else None
    else:
        features = results

    if features is None:
        raise RuntimeError("Essentia returned no features")

    return essentia_features_to_dict(features)


def find_source_audio_files(source_dir: Path) -> list[Path]:
    """Find supported source audio files in SourceFiles directory."""
    if not source_dir.exists():
        return []

    patterns = ("*.wav", "*.wave", "*.aif", "*.aiff", "*.mp3", "*.flac", "*.m4a")
    files = []
    for pattern in patterns:
        files.extend(source_dir.glob(pattern))

    # De-duplicate and sort for stable output
    return sorted(set(files), key=lambda p: p.name.lower())


def energy_score(f: dict) -> float:
    """
    Composite energy score from Essentia features.
    Returns 0.00–100.00  (soft/ambient → hard/driving)

    Weight breakdown:
      Rhythm   55%  — beat intensity, consistency, punch, BPM, danceability
      Spectral 30%  — sonic density, mastering loudness
      Tonal    15%  — harmonic presence, repetitive structure
    """

    # ── RHYTHM ────────────────────────────────────────────────────────────

    # Beat loudness: ~0.05 = soft, ~0.20 = hard floor-filler
    beat_mean  = f["rhythm.beats_loudness.mean"]
    beat_stdev = f["rhythm.beats_loudness.stdev"]
    loudness    = np.clip(beat_mean / 0.20, 0.0, 1.0)

    # Consistency: low CV = relentlessly even beat power
    cv          = beat_stdev / (beat_mean + 1e-9)
    consistency = 1.0 / (1.0 + cv)
    beat_score  = (loudness + consistency) / 2.0          # (25%)

    # Punch/attack: 2nd derivative = sharpness of transient hits
    punch       = np.clip(f["rhythm.beats_loudness.dmean2"] / 0.08, 0.0, 1.0)  # (5%)

    # Tempo: 60 BPM = 0.0 → 180 BPM = 1.0
    bpm_score   = np.clip((f["rhythm.bpm"] - 60.0) / 120.0, 0.0, 1.0)          # (15%)

    # Danceability: Essentia range ~0–3
    dance_score = np.clip(f["rhythm.danceability"] / 3.0, 0.0, 1.0)             # (10%)


    # ── SPECTRAL ──────────────────────────────────────────────────────────

    # Spectral energy mean: sonic density between beats too
    # ~0.01 = thin/sparse, ~0.12+ = dense/saturated
    spectral    = np.clip(f["lowlevel.spectral_energy.mean"] / 0.12, 0.0, 1.0)  # (20%)

    # Average loudness: 0.7 = natural dynamics, ~1.0 = brick-wall limited
    # Good proxy for "pushed" dance tracks; weighted below spectral
    avg_loud    = np.clip(f["lowlevel.average_loudness"], 0.0, 1.0)              # (10%)


    # ── TONAL ─────────────────────────────────────────────────────────────

    # Chord strength: how clearly the audio matches harmonic templates (0–1)
    chord_str   = np.clip(f["tonal.chords_strength.mean"], 0.0, 1.0)            # (8%)

    # Chord stability: low change rate = hypnotic / repetitive (house, techno)
    # changes_rate ~0.03 = barely changes, ~0.3 = changes every few bars
    # Inverted: stable = higher score
    chord_stab  = 1.0 - np.clip(f["tonal.chords_changes_rate"] / 0.30, 0.0, 1.0)  # (7%)


    # ── WEIGHTED SUM ──────────────────────────────────────────────────────
    score = (
        0.25 * beat_score   +   # loud + consistent beats
        0.05 * punch        +   # percussive snap/attack
        0.15 * bpm_score    +   # tempo
        0.10 * dance_score  +   # rhythmic regularity
        0.20 * spectral     +   # overall sonic density
        0.10 * avg_loud     +   # mastering / compression level
        0.08 * chord_str    +   # tonal presence
        0.07 * chord_stab       # repetitive harmonic structure
    )

    return round(score * 100.0, 2)


def find_essentia_jsons(logs_dir: Path) -> list[tuple[str, Path]]:
    """
    Find all Essentia JSON output files in Logs directory.
    Returns list of (track_identifier, path) tuples, sorted by track name.
    """
    essentia_files = []
    
    if not logs_dir.exists():
        print(f"[ERROR] Logs directory not found: {logs_dir}")
        return []
    
    for json_file in sorted(logs_dir.glob("*.essentia.json")):
        track_id = json_file.stem.replace(".essentia", "")
        essentia_files.append((track_id, json_file))
    
    return essentia_files


def process_directory(logs_dir: Path, verbose: bool = False) -> list[dict]:
    """
    Process all Essentia JSON files in logs directory.
    Returns list of dicts with track_id and energy_score.
    """
    essentia_files = find_essentia_jsons(logs_dir)
    
    if not essentia_files:
        print(f"[WARN] No Essentia JSON files found in {logs_dir}")
        return []
    
    results = []
    
    for track_id, json_path in essentia_files:
        features = load_essentia_json(json_path)
        if features is None:
            continue
        
        try:
            score = energy_score(features)
            results.append({
                "track_id": track_id,
                "energy_score": score,
                "json_path": str(json_path)
            })
            if verbose:
                print(f"  [OK] {track_id:50s} -> {score:6.2f}")
        except KeyError as e:
            print(f"  [ERR] {track_id:50s} -> missing feature: {e}")
        except Exception as e:
            print(f"  [ERR] {track_id:50s} -> error: {e}")
    
    return results


def process_source_files(
    source_dir: Path,
    logs_dir: Path,
    verbose: bool = False,
    save_json: bool = True,
) -> list[dict]:
    """
    Analyze source audio files directly with Essentia and compute energy scores.
    Optionally writes per-track feature JSON files in logs_dir.
    """
    source_files = find_source_audio_files(source_dir)
    if not source_files:
        print(f"[WARN] No source audio files found in {source_dir}")
        return []

    if save_json:
        logs_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for audio_path in source_files:
        track_id = audio_path.stem
        try:
            raw_features = extract_essentia_for_file(audio_path)
            flat_features = flatten_essentia_features(raw_features)
            score = energy_score(flat_features)

            json_path = logs_dir / f"{track_id}.essentia.json"
            if save_json:
                json_path.write_text(json.dumps(raw_features, indent=2), encoding="utf-8")

            results.append(
                {
                    "track_id": track_id,
                    "energy_score": score,
                    "source_path": str(audio_path),
                    "json_path": str(json_path),
                }
            )
            if verbose:
                print(f"  [OK] {track_id:50s} -> {score:6.2f}")
        except KeyError as e:
            print(f"  [ERR] {track_id:50s} -> missing feature: {e}")
        except Exception as e:
            print(f"  [ERR] {track_id:50s} -> extraction failed: {e}")

    return results


def print_results_table(results: list[dict]) -> None:
    """Pretty-print results as ASCII table."""
    if not results:
        print("No results to display.")
        return
    
    # Sort by energy score descending
    results = sorted(results, key=lambda x: x["energy_score"], reverse=True)
    
    print("\n" + "=" * 80)
    print(f"{'Track ID':<50} {'Energy Score':>20}")
    print("=" * 80)
    
    for result in results:
        score = result["energy_score"]
        if score < 33:
            band = "SOFT"
        elif score < 66:
            band = "MID"
        else:
            band = "HARD"

        print(f"{result['track_id']:<50} {band:>6} {score:>12.2f}")
    
    print("=" * 80)
    print(f"Processed: {len(results)} tracks")
    avg_score = np.mean([r["energy_score"] for r in results])
    print(f"Average energy: {avg_score:.2f}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Score tracks by energy using Essentia features",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze SourceFiles directly with Essentia and score tracks
  python essentia_energy.py ~/Music/DJ-Set-Prep

    # Reuse existing .essentia.json files in Logs (skip extraction)
    python essentia_energy.py ~/Music/DJ-Set-Prep --from-json

    # Verbose output with per-track details during extraction
  python essentia_energy.py ~/Music/DJ-Set-Prep --verbose

  # Save results to CSV
  python essentia_energy.py ~/Music/DJ-Set-Prep --csv energy-scores.csv
        """
    )
    
    parser.add_argument(
        "prep_root",
        type=Path,
        help="Path to DJ-Set-Prep root directory"
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Override path to Logs directory (default: prep_root/Logs)"
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Override path to SourceFiles directory (default: prep_root/SourceFiles)"
    )
    parser.add_argument(
        "--from-json",
        action="store_true",
        help="Skip Essentia extraction and score from existing .essentia.json files"
    )
    parser.add_argument(
        "--no-save-json",
        action="store_true",
        help="When extracting, do not persist per-track .essentia.json files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-track details during processing"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Export results to CSV file"
    )
    
    args = parser.parse_args()
    
    source_dir = args.source_dir if args.source_dir else args.prep_root / "SourceFiles"
    logs_dir = args.logs_dir if args.logs_dir else args.prep_root / "Logs"

    if args.from_json:
        print(f"[INFO] Scoring from existing Essentia JSON files in: {logs_dir}\n")
        results = process_directory(logs_dir, verbose=args.verbose)
    else:
        print(f"[INFO] Analyzing source audio files in: {source_dir}")
        print(f"[INFO] JSON output directory: {logs_dir}\n")
        results = process_source_files(
            source_dir=source_dir,
            logs_dir=logs_dir,
            verbose=args.verbose,
            save_json=not args.no_save_json,
        )
    
    # Display results
    if results:
        print_results_table(results)
        
        # Export to CSV if requested
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            with open(args.csv, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["track_id", "energy_score"])
                writer.writeheader()
                for result in sorted(results, key=lambda x: x["energy_score"], reverse=True):
                    writer.writerow({
                        "track_id": result["track_id"],
                        "energy_score": result["energy_score"]
                    })
            print(f"[OK] Results saved to: {args.csv}\n")
    else:
        print("[ERROR] No tracks were successfully scored.")


if __name__ == "__main__":
    main()
