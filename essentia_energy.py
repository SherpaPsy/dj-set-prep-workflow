import argparse
import csv
import json
from pathlib import Path
from typing import Any, Optional, cast

import numpy as np
import pandas as pd  # type: ignore[import-not-found]

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
            elif isinstance(value, (list, tuple)) and key.endswith(("mean", "stdev", "dmean2", "max")):
                # For statistical keys, compute the statistic from array
                if isinstance(value, (list, tuple)):
                    if key.endswith("mean"):
                        flat[full_key] = float(np.mean(value)) if value else 0.0
                    elif key.endswith("stdev"):
                        flat[full_key] = float(np.std(value)) if value else 0.0
                    elif key.endswith("max"):
                        flat[full_key] = float(np.max(value)) if value else 0.0
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


# ── Component Extraction & Scaling ────────────────────────────────────────

WEIGHTS = {
    "beat":     0.40,
    "punch":    0.10,
    "spectral": 0.20,
    "dance":    0.15,
    "harmonic": 0.15,
}


def raw_components(f: dict) -> dict:
    """Extract unnormalized component scores (raw math, no clipping)."""
    beat_mean  = f["rhythm.beats_loudness.mean"]
    beat_stdev = f["rhythm.beats_loudness.stdev"]
    cv         = beat_stdev / (beat_mean + 1e-9)
    intensity  = beat_mean
    consistency = 1.0 / (1.0 + cv)
    beat_raw   = (intensity * 0.6) + (consistency * 0.4)

    return {
        "beat":     beat_raw,
        "punch":    f["rhythm.beats_loudness.dmean2"],
        "spectral": (f["lowlevel.spectral_energy.mean"] * 0.7 +
                     f["lowlevel.spectral_energy.max"]  * 0.3),
        "dance":    f["rhythm.danceability"],
        "harmonic": (f["tonal.chords_strength.mean"]    * 0.5 +
                     f["tonal.chords_changes_rate"]      * 0.5),
    }


def fit_scaler(all_features: list[dict], percentile_low: int = 5, percentile_high: int = 95) -> dict:
    """
    Compute per-component (p5, p95) bounds from library.
    Uses percentiles instead of min/max to be robust to outliers.
    """
    rows = [raw_components(f) for f in all_features]
    df = pd.DataFrame(rows)
    scaler = {}
    for col in df.columns:
        scaler[col] = {
            "low":  float(np.percentile(df[col], percentile_low)),
            "high": float(np.percentile(df[col], percentile_high)),
        }
    return scaler


def energy_score(f: dict, scaler: Optional[dict] = None) -> float:
    """
    Library-fitted energy score for house/techno subgenres.
    Returns 0.00–100.00  (deep/atmospheric → hard/driving)

    If scaler is provided, normalizes using library p5/p95 bounds.
    If scaler is None, uses fixed fallback bounds (backward compatible).

    Weights:
      40%  Beat intensity & consistency  (core driving force)
      10%  Percussive punch              (afro/tech snap vs. smooth deep)
      20%  Spectral density              (thin/spacious vs. full/dense)
      15%  Danceability                  (groove strength & regularity)
      15%  Harmonic activity             (melodic complexity vs. minimal)
    """
    raw = raw_components(f)
    normed = {}

    if scaler is None:
        # Fallback: use fixed normalization bounds
        scaler = {
            "beat":     {"low": 0.0,  "high": 0.18},
            "punch":    {"low": 0.0,  "high": 0.08},
            "spectral": {"low": 0.0,  "high": 0.25},
            "dance":    {"low": 0.0,  "high": 3.0},
            "harmonic": {"low": 0.0,  "high": 0.5},
        }

    for key, val in raw.items():
        lo = scaler[key]["low"]
        hi = scaler[key]["high"]
        normed[key] = np.clip((val - lo) / (hi - lo + 1e-9), 0.0, 1.0)

    score = sum(WEIGHTS[k] * normed[k] for k in WEIGHTS)
    return int(round(score * 100.0))


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
    Fits scaler on all features, then scores all tracks.
    Returns list of dicts with track_id and energy_score.
    """
    essentia_files = find_essentia_jsons(logs_dir)
    
    if not essentia_files:
        print(f"[WARN] No Essentia JSON files found in {logs_dir}")
        return []
    
    # Step 1: Load all features
    print(f"[INFO] Loading {len(essentia_files)} feature files...")
    all_features = []
    track_map = {}  # map track_id -> (json_path, features)
    
    for track_id, json_path in essentia_files:
        features = load_essentia_json(json_path)
        if features is None:
            continue
        all_features.append(features)
        track_map[track_id] = (json_path, features)
    
    if not all_features:
        print(f"[ERR] No valid feature files could be loaded")
        return []
    
    # Step 2: Fit scaler on all features
    print(f"[INFO] Fitting scaler on {len(all_features)} tracks...")
    scaler = fit_scaler(all_features)
    
    # Step 3: Score all tracks using fitted scaler
    results = []
    for track_id, (json_path, features) in track_map.items():
        try:
            score = energy_score(features, scaler)
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
    Analyze source audio files directly with Essentia.
    Fits scaler on all features, then scores all tracks.
    Optionally writes per-track feature JSON files in logs_dir.
    """
    source_files = find_source_audio_files(source_dir)
    if not source_files:
        print(f"[WARN] No source audio files found in {source_dir}")
        return []

    if save_json:
        logs_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Extract all features from source files
    print(f"[INFO] Extracting Essentia features from {len(source_files)} files...")
    all_features = []
    track_map = {}  # map track_id -> (audio_path, raw_features, flat_features, json_path)
    
    for audio_path in source_files:
        track_id = audio_path.stem
        try:
            raw_features = extract_essentia_for_file(audio_path)
            flat_features = flatten_essentia_features(raw_features)
            json_path = logs_dir / f"{track_id}.essentia.json"
            
            all_features.append(flat_features)
            track_map[track_id] = (audio_path, raw_features, flat_features, json_path)
        except Exception as e:
            print(f"  [ERR] {track_id:50s} -> extraction failed: {e}")
    
    if not all_features:
        print(f"[ERR] No features could be extracted")
        return []
    
    # Step 2: Fit scaler on all features
    print(f"[INFO] Fitting scaler on {len(all_features)} tracks...")
    scaler = fit_scaler(all_features)
    
    # Step 3: Save JSON and score all tracks using fitted scaler
    results = []
    for track_id, (audio_path, raw_features, flat_features, json_path) in sorted(track_map.items()):
        try:
            score = energy_score(flat_features, scaler)
            
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
        except Exception as e:
            print(f"  [ERR] {track_id:50s} -> scoring failed: {e}")

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

        print(f"{result['track_id']:<50} {band:>6} {score:>12d}")
    
    print("=" * 80)
    print(f"Processed: {len(results)} tracks")
    avg_score = np.mean([r["energy_score"] for r in results])
    print(f"Average energy: {int(round(avg_score))}")
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
