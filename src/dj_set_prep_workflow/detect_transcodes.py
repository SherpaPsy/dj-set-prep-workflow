from __future__ import annotations

import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from bitrater import AudioQualityAnalyzer
from bitrater.types import AnalysisResult

# bitrater's LOSSLESS_CONTAINERS constant recognizes ".aiff" but not ".aif", so a
# file named "<stem>.aif" (this workflow's rendered/tagged output naming) gets
# mis-scored as a lossy container internally. Feed those through a same-target
# ".aiff" symlink so bitrater classifies them as lossless before comparing against
# the detected spectral quality.
ALIASED_LOSSLESS_SUFFIXES = {".aif": ".aiff"}

# macOS's Accelerate BLAS backend raises these on ordinary matmuls with no NaN/Inf
# in either operand or the result (verified against bitrater's dl_inference.py
# feature extraction) - a known numpy-on-Apple-Silicon quirk, not bad audio data.
_SPURIOUS_MATMUL_WARNINGS = (
    "divide by zero encountered in matmul",
    "overflow encountered in matmul",
    "invalid value encountered in matmul",
)

# Below this confidence, treat a non-transcode verdict as worth a second listen
# rather than a clean pass.
LOW_CONFIDENCE_THRESHOLD = 0.6


@dataclass(slots=True)
class TranscodeFinding:
    file_name: str
    full_path: str
    extension: str
    stated_class: str
    stated_bitrate_kbps: int | None
    detected_format: str | None
    detected_bitrate_kbps: int | None
    confidence: float | None
    verdict: str
    note: str


def _analysis_path(path: Path, tmp_dir: Path) -> Path:
    aliased_suffix = ALIASED_LOSSLESS_SUFFIXES.get(path.suffix.lower())
    if aliased_suffix is None:
        return path

    aliased_path = tmp_dir / f"{path.stem}{aliased_suffix}"
    os.symlink(path.resolve(), aliased_path)
    return aliased_path


def _error_finding(path: Path, note: str) -> TranscodeFinding:
    return TranscodeFinding(
        file_name=path.name,
        full_path=str(path),
        extension=path.suffix.lower(),
        stated_class="UNKNOWN",
        stated_bitrate_kbps=None,
        detected_format=None,
        detected_bitrate_kbps=None,
        confidence=None,
        verdict="ERROR",
        note=note,
    )


def classify_result(result: AnalysisResult) -> tuple[str, str]:
    if result.warnings:
        note = "; ".join(result.warnings)
    else:
        note = f"Content matches stated {result.stated_class} encoding (confidence {result.confidence:.0%})."

    if result.is_transcode:
        return "LIKELY_TRANSCODED", note

    if result.confidence < LOW_CONFIDENCE_THRESHOLD:
        return "UNCERTAIN", note

    return "OK", note


def analyze_file(analyzer: AudioQualityAnalyzer, path: Path) -> TranscodeFinding:
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            analysis_path = _analysis_path(path, Path(tmp_dir))
            with warnings.catch_warnings():
                for message in _SPURIOUS_MATMUL_WARNINGS:
                    warnings.filterwarnings("ignore", message=message, category=RuntimeWarning)
                result = analyzer.analyze_file(str(analysis_path))
    except Exception as exc:
        return _error_finding(path, f"Analysis failed: {exc}")

    if result is None:
        return _error_finding(path, "bitrater could not decode/classify this file.")

    verdict, note = classify_result(result)
    return TranscodeFinding(
        file_name=path.name,
        full_path=str(path),
        extension=path.suffix.lower(),
        stated_class=result.stated_class,
        stated_bitrate_kbps=result.stated_bitrate,
        detected_format=result.original_format,
        detected_bitrate_kbps=result.original_bitrate,
        confidence=result.confidence,
        verdict=verdict,
        note=note,
    )


def format_transcode_report(findings: list[TranscodeFinding]) -> str:
    header = f"{'File':<45} {'Ext':<5} {'Stated':>10} {'Detected':>10} {'Conf':>6}  Verdict"
    lines = [header, "-" * len(header)]

    for finding in findings:
        stated = finding.stated_class or "n/a"
        detected = finding.detected_format or "n/a"
        conf = f"{finding.confidence:.0%}" if finding.confidence is not None else "n/a"
        name = finding.file_name if len(finding.file_name) <= 45 else finding.file_name[:42] + "..."
        lines.append(f"{name:<45} {finding.extension:<5} {stated:>10} {detected:>10} {conf:>6}  {finding.verdict}")

    flagged = [f for f in findings if f.verdict == "LIKELY_TRANSCODED"]
    uncertain = [f for f in findings if f.verdict == "UNCERTAIN"]
    errored = [f for f in findings if f.verdict == "ERROR"]

    lines.append("")
    if flagged:
        lines.append(f"FLAGGED: {len(flagged)} file(s) likely transcoded from a lower bitrate source:")
        for finding in flagged:
            lines.append(f"  - {finding.file_name}: {finding.note}")
    else:
        lines.append("No files flagged as likely transcoded.")

    if uncertain:
        lines.append("")
        lines.append(f"UNCERTAIN: {len(uncertain)} file(s) had a low-confidence classification:")
        for finding in uncertain:
            lines.append(f"  - {finding.file_name}: {finding.note}")

    if errored:
        lines.append("")
        lines.append(f"ERRORS: {len(errored)} file(s) could not be analyzed:")
        for finding in errored:
            lines.append(f"  - {finding.file_name}: {finding.note}")

    return "\n".join(lines)


def run_transcode_scan(source_files: list[Path]) -> list[TranscodeFinding]:
    print("[START] Transcode detection scan")
    print("[INFO] Loading bitrater model (one-time cost per run)...")
    analyzer = AudioQualityAnalyzer()
    findings = [analyze_file(analyzer, path) for path in source_files]
    print(format_transcode_report(findings))
    print("[DONE] Transcode detection scan")
    return findings


def pause_for_transcode_review() -> None:
    input("\n[PAUSE] Review the transcode report above. Press Enter to continue with the flow...")
