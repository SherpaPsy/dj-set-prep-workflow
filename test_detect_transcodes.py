from __future__ import annotations

import os
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory

from bitrater.types import AnalysisResult

from dj_set_prep_workflow.detect_transcodes import (
    TranscodeFinding,
    _analysis_path,
    analyze_file,
    classify_result,
    format_transcode_report,
)


def _make_result(**overrides: object) -> AnalysisResult:
    defaults: dict[str, object] = {
        "filename": "track.mp3",
        "file_format": "mp3",
        "original_format": "320",
        "original_bitrate": 320,
        "confidence": 0.9,
        "is_transcode": False,
        "stated_class": "320",
        "detected_cutoff": 0,
        "quality_gap": 0,
        "transcoded_from": None,
        "stated_bitrate": 320,
        "warnings": [],
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)  # type: ignore[arg-type]


class _FakeAnalyzer:
    def __init__(self, result: AnalysisResult | None = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.received_paths: list[str] = []

    def analyze_file(self, file_path: str) -> AnalysisResult | None:
        self.received_paths.append(file_path)
        if self._exc is not None:
            raise self._exc
        return self._result


class AnalysisPathAliasingTests(unittest.TestCase):
    def test_aif_is_aliased_to_aiff(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "input" / "track.aif"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"fake-audio")
            alias_dir = Path(tmp_dir) / "alias"
            alias_dir.mkdir()

            result = _analysis_path(source, alias_dir)

            self.assertEqual(result.suffix, ".aiff")
            self.assertEqual(result.stem, "track")
            self.assertTrue(result.is_symlink())
            self.assertEqual(result.resolve(), source.resolve())

    def test_aliased_symlink_resolves_when_source_path_is_relative(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_dir = Path(tmp_dir) / "input"
            source_dir.mkdir()
            (source_dir / "track.aif").write_bytes(b"fake-audio")
            alias_dir = Path(tmp_dir) / "alias"
            alias_dir.mkdir()

            original_cwd = Path.cwd()
            os.chdir(tmp_dir)
            try:
                relative_source = Path("input") / "track.aif"
                self.assertFalse(relative_source.is_absolute())

                result = _analysis_path(relative_source, alias_dir)

                self.assertTrue(result.exists(), "symlink target should resolve even from a relative source path")
            finally:
                os.chdir(original_cwd)

    def test_other_extensions_are_unchanged(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.mp3"
            source.write_bytes(b"fake-audio")

            result = _analysis_path(source, Path(tmp_dir))

            self.assertEqual(result, source)


class ClassifyResultTests(unittest.TestCase):
    def test_transcode_flag_wins_regardless_of_confidence(self) -> None:
        result = _make_result(
            is_transcode=True,
            confidence=0.95,
            stated_class="LOSSLESS",
            original_format="128",
            transcoded_from="128",
            warnings=["File appears to be transcoded from 128 (quality gap: 6)"],
        )

        verdict, note = classify_result(result)

        self.assertEqual(verdict, "LIKELY_TRANSCODED")
        self.assertIn("128", note)

    def test_low_confidence_non_transcode_is_uncertain(self) -> None:
        result = _make_result(is_transcode=False, confidence=0.4)

        verdict, _ = classify_result(result)

        self.assertEqual(verdict, "UNCERTAIN")

    def test_high_confidence_non_transcode_is_ok(self) -> None:
        result = _make_result(is_transcode=False, confidence=0.9, warnings=[])

        verdict, note = classify_result(result)

        self.assertEqual(verdict, "OK")
        self.assertIn("320", note)

    def test_warnings_surface_in_note_even_when_ok(self) -> None:
        result = _make_result(
            is_transcode=False,
            confidence=0.9,
            warnings=["Stated bitrate (2116 kbps) much higher than detected (320 kbps) - possible upsampled file"],
        )

        _, note = classify_result(result)

        self.assertIn("possible upsampled file", note)


class AnalyzeFileTests(unittest.TestCase):
    def test_maps_analysis_result_onto_finding(self) -> None:
        result = _make_result(
            is_transcode=True,
            stated_class="LOSSLESS",
            original_format="192",
            original_bitrate=192,
            confidence=0.85,
            transcoded_from="192",
            stated_bitrate=1411,
            warnings=["File appears to be transcoded from 192 (quality gap: 4)"],
        )
        analyzer = _FakeAnalyzer(result=result)

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.aiff"
            source.write_bytes(b"fake-audio")

            finding = analyze_file(analyzer, source)

        self.assertEqual(finding.verdict, "LIKELY_TRANSCODED")
        self.assertEqual(finding.detected_format, "192")
        self.assertEqual(finding.detected_bitrate_kbps, 192)
        self.assertEqual(finding.stated_class, "LOSSLESS")
        self.assertEqual(finding.confidence, 0.85)

    def test_aif_source_is_passed_to_analyzer_as_aiff(self) -> None:
        analyzer = _FakeAnalyzer(result=_make_result())

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "rendered.aif"
            source.write_bytes(b"fake-audio")

            analyze_file(analyzer, source)

        self.assertEqual(len(analyzer.received_paths), 1)
        self.assertTrue(analyzer.received_paths[0].endswith("rendered.aiff"))

    def test_none_result_is_reported_as_error(self) -> None:
        analyzer = _FakeAnalyzer(result=None)

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.mp3"
            source.write_bytes(b"fake-audio")

            finding = analyze_file(analyzer, source)

        self.assertEqual(finding.verdict, "ERROR")

    def test_exception_is_reported_as_error(self) -> None:
        analyzer = _FakeAnalyzer(exc=RuntimeError("boom"))

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.mp3"
            source.write_bytes(b"fake-audio")

            finding = analyze_file(analyzer, source)

        self.assertEqual(finding.verdict, "ERROR")
        self.assertIn("boom", finding.note)


class SpuriousWarningSuppressionTests(unittest.TestCase):
    """These specific RuntimeWarnings fire on every file on macOS's Accelerate BLAS
    backend during bitrater's internal matmuls, even though neither operand nor the
    result contains NaN/Inf (verified directly against bitrater's feature extraction
    code). They're noise, not a signal about bad audio - suppress them, but only them.
    """

    def test_spurious_matmul_warnings_are_suppressed(self) -> None:
        class _WarningAnalyzer:
            def analyze_file(self, file_path: str) -> AnalysisResult | None:
                warnings.warn("invalid value encountered in matmul", RuntimeWarning)
                warnings.warn("overflow encountered in matmul", RuntimeWarning)
                warnings.warn("divide by zero encountered in matmul", RuntimeWarning)
                return _make_result()

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.mp3"
            source.write_bytes(b"fake-audio")

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_file(_WarningAnalyzer(), source)

        matmul_warnings = [w for w in caught if "matmul" in str(w.message)]
        self.assertEqual(matmul_warnings, [])

    def test_unrelated_warnings_still_surface(self) -> None:
        class _WarningAnalyzer:
            def analyze_file(self, file_path: str) -> AnalysisResult | None:
                warnings.warn("some other real warning", RuntimeWarning)
                return _make_result()

        with TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "track.mp3"
            source.write_bytes(b"fake-audio")

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                analyze_file(_WarningAnalyzer(), source)

        self.assertTrue(any("some other real warning" in str(w.message) for w in caught))


class FormatTranscodeReportTests(unittest.TestCase):
    def test_report_lists_flagged_and_uncertain_files(self) -> None:
        findings = [
            TranscodeFinding(
                file_name="track-a.mp3",
                full_path="/music/track-a.mp3",
                extension=".mp3",
                stated_class="320",
                stated_bitrate_kbps=320,
                detected_format="128",
                detected_bitrate_kbps=128,
                confidence=0.9,
                verdict="LIKELY_TRANSCODED",
                note="Tagged as 320 but content matches a ~128kbps source.",
            ),
            TranscodeFinding(
                file_name="track-b.mp3",
                full_path="/music/track-b.mp3",
                extension=".mp3",
                stated_class="320",
                stated_bitrate_kbps=320,
                detected_format="320",
                detected_bitrate_kbps=320,
                confidence=0.45,
                verdict="UNCERTAIN",
                note="Low-confidence classification.",
            ),
        ]

        report = format_transcode_report(findings)

        self.assertIn("track-a.mp3", report)
        self.assertIn("track-b.mp3", report)
        self.assertIn("FLAGGED: 1 file(s)", report)
        self.assertIn("UNCERTAIN: 1 file(s)", report)


if __name__ == "__main__":
    unittest.main()
