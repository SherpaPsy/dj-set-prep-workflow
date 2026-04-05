from __future__ import annotations

import aifc
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mutagen.aiff import AIFF
from mutagen.id3 import APIC, COMM, TALB, TCON, TIT2, TPE1, TPE2, TDRC, TXXX

from dj_set_prep_workflow.run_set_prep_flow import (
    append_suffix_to_title,
    build_prep_paths,
    clean_working_directories,
    copy_processed_to_tagged,
    extract_essentia_summary,
    sync_reaper_project_to_input,
    write_tags_to_processed_aiff,
)
from dj_set_prep_workflow.tag_set_mp3s import TrackEntry


def _create_test_aiff(path: Path) -> None:
    with aifc.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(2)
        audio_file.setsampwidth(2)
        audio_file.setframerate(44100)
        audio_file.writeframes(b"\x00\x00\x00\x00" * 32)


class RunSetPrepFlowTests(unittest.TestCase):
    def test_write_tags_replaces_existing_invalid_frames(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            rendered_aiff = Path(tmp_dir) / "rendered.aif"
            _create_test_aiff(rendered_aiff)

            audio = AIFF(rendered_aiff)
            audio.add_tags()
            audio.tags.setall("TIT2", [TIT2(encoding=3, text=["stale title"])])
            audio.tags.setall("TPE1", [TPE1(encoding=3, text=["stale artist"])])
            audio.tags.setall("TXXX:legacy", [TXXX(encoding=3, desc="legacy", text=["bad"])])
            audio.save()

            result = write_tags_to_processed_aiff(
                rendered_aiff,
                source_file=rendered_aiff,
                source_tags={
                    "title": ["Source Title"],
                    "artist": ["Source Artist"],
                    "genre": ["Progressive House"],
                    "album": ["Source Album"],
                },
                metadata_entry=TrackEntry(
                    title="Ignored Metadata Title",
                    artist="Metadata Artist",
                    label="Bedrock",
                    year="2024",
                ),
                essentia_comment="essentia:bpm=124;key=8A",
                default_genre="Electronic",
                dry_run=False,
            )

            self.assertEqual(result["TIT2"], ["Source Title [Bedrock 2024]"])
            self.assertEqual(result["TPE1"], ["Source Artist"])
            self.assertEqual(result["TPE2"], ["Source Artist"])
            self.assertEqual(result["TCON"], ["Progressive House"])
            self.assertEqual(result["TALB"], ["Source Album"])
            self.assertEqual(result["TDRC"], ["2024"])

            rewritten = AIFF(rendered_aiff).tags
            self.assertEqual(str(rewritten.get("TIT2")), "Source Title [Bedrock 2024]")
            self.assertEqual(str(rewritten.get("TPE1")), "Source Artist")
            self.assertEqual(str(rewritten.get("TPE2")), "Source Artist")
            self.assertEqual(str(rewritten.get("TCON")), "Progressive House")
            self.assertEqual(str(rewritten.get("TALB")), "Source Album")
            self.assertEqual(str(rewritten.get("TDRC")), "2024")
            self.assertIsNone(rewritten.get("TXXX:legacy"))

            comments = rewritten.getall("COMM")
            self.assertEqual(len(comments), 2)
            descs = sorted(comment.desc for comment in comments)
            self.assertEqual(descs, ["", "essentia"])
            for comment in comments:
                self.assertEqual(list(comment.text), ["essentia:bpm=124;key=8A"])

    def test_write_tags_preserves_cover_art(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_aiff = Path(tmp_dir) / "source.aif"
            rendered_aiff = Path(tmp_dir) / "rendered.aif"
            _create_test_aiff(source_aiff)
            _create_test_aiff(rendered_aiff)

            source_audio = AIFF(source_aiff)
            source_audio.add_tags()
            source_audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"\xff\xd8\xff\xd9"))
            source_audio.save()

            write_tags_to_processed_aiff(
                rendered_aiff,
                source_file=source_aiff,
                source_tags={
                    "title": ["Source Title"],
                    "artist": ["Source Artist"],
                },
                metadata_entry=TrackEntry(
                    title="Ignored Metadata Title",
                    artist="Metadata Artist",
                    label="Bedrock",
                    year="2024",
                ),
                essentia_comment="essentia:bpm=124;key=8A",
                default_genre="Electronic",
                dry_run=False,
            )

            tagged = AIFF(rendered_aiff).tags
            artwork = tagged.getall("APIC")
            self.assertEqual(len(artwork), 1)
            self.assertEqual(artwork[0].mime, "image/jpeg")
            self.assertEqual(artwork[0].data, b"\xff\xd8\xff\xd9")

    def test_append_suffix_avoids_duplicate_bracketed_suffix(self) -> None:
        title = "Source Title [Bedrock 2024]"
        suffix = "[Bedrock 2024]"
        self.assertEqual(append_suffix_to_title(title, suffix), title)

    def test_append_suffix_keeps_existing_trailing_label_year(self) -> None:
        title = "Ultraviolet (Original Mix) [Fluxo 2026]"
        suffix = "[Drillers 2026]"
        self.assertEqual(append_suffix_to_title(title, suffix), title)

    def test_extract_essentia_summary_omits_prefix_and_bpm(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            payload = {
                "rhythm.bpm": 122,
                "rhythm.danceability": 2.1,
                "tonal.key_temperley.key": "F",
                "tonal.key_temperley.scale": "minor",
                "tonal.chords_key": "C",
                "tonal.chords_scale": "major",
            }
            json_path = Path(tmp_dir) / "essentia.json"
            json_path.write_text(json.dumps(payload), encoding="utf-8")

            summary = extract_essentia_summary(json_path)
            self.assertEqual(summary, "key=4A;chords=8B;energy=2")

    def test_copy_processed_to_tagged_creates_tagged_output(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            processed_aiff = temp_root / "ProcessedFiles" / "track.aif"
            tagged_dir = temp_root / "TaggedFiles"
            processed_aiff.parent.mkdir(parents=True, exist_ok=True)
            tagged_dir.mkdir(parents=True, exist_ok=True)

            _create_test_aiff(processed_aiff)

            tagged_aiff = copy_processed_to_tagged(processed_aiff, tagged_dir, dry_run=False)

            self.assertEqual(tagged_aiff, tagged_dir / "track.aif")
            self.assertTrue(tagged_aiff.exists())
            self.assertEqual(tagged_aiff.read_bytes(), processed_aiff.read_bytes())

    def test_clean_working_directories_clears_only_outputs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = build_prep_paths(root)
            for directory in [
                paths.converted_aiff,
                paths.processed_aiff,
                paths.tagged_aiff,
                paths.logs,
                paths.metadata,
            ]:
                directory.mkdir(parents=True, exist_ok=True)

            (paths.converted_aiff / "a.txt").write_text("x", encoding="utf-8")
            (paths.processed_aiff / "b.txt").write_text("x", encoding="utf-8")
            (paths.tagged_aiff / "c.txt").write_text("x", encoding="utf-8")
            (paths.logs / "keep.log").write_text("keep", encoding="utf-8")
            (paths.metadata / "keep.txt").write_text("keep", encoding="utf-8")

            clean_working_directories(paths, dry_run=False)

            self.assertEqual(list(paths.converted_aiff.iterdir()), [])
            self.assertEqual(list(paths.processed_aiff.iterdir()), [])
            self.assertEqual(list(paths.tagged_aiff.iterdir()), [])
            self.assertTrue((paths.logs / "keep.log").exists())
            self.assertTrue((paths.metadata / "keep.txt").exists())

    def test_clean_working_directories_dry_run_keeps_outputs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = build_prep_paths(root)
            for directory in [
                paths.converted_aiff,
                paths.processed_aiff,
                paths.tagged_aiff,
            ]:
                directory.mkdir(parents=True, exist_ok=True)

            (paths.converted_aiff / "a.txt").write_text("x", encoding="utf-8")
            (paths.processed_aiff / "b.txt").write_text("x", encoding="utf-8")
            (paths.tagged_aiff / "c.txt").write_text("x", encoding="utf-8")

            clean_working_directories(paths, dry_run=True)

            self.assertTrue((paths.converted_aiff / "a.txt").exists())
            self.assertTrue((paths.processed_aiff / "b.txt").exists())
            self.assertTrue((paths.tagged_aiff / "c.txt").exists())

    def test_sync_reaper_project_updates_source_selection_and_length(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_aiff = root / "input.aiff"
            output_aif = root / "output.aif"
            reaper_project = root / "DJ Set Prep.rpp"

            with aifc.open(str(input_aiff), "wb") as audio_file:
                audio_file.setnchannels(2)
                audio_file.setsampwidth(2)
                audio_file.setframerate(44100)
                audio_file.writeframes(b"\x00\x00\x00\x00" * 44100)

            reaper_project.write_text(
                "\n".join(
                    [
                        '<REAPER_PROJECT 0.1 "7.61/win64" 0 0',
                        f'  RENDER_FILE "{root / "stale-output.aif"}"',
                        '  RENDER_RANGE 1 0 0 0 1000',
                        '  SELECTION 0 0',
                        '  SELECTION2 0 0',
                        '  <TRACK',
                        '    <ITEM',
                        '      LENGTH 468.29269841269843',
                        '      <SOURCE WAVE',
                        f'        FILE "{root / "stale-input.aiff"}"',
                        '      >',
                        '    >',
                        '  >',
                        '>',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            duration = sync_reaper_project_to_input(
                reaper_project=reaper_project,
                input_audio=input_aiff,
                output_audio=output_aif,
                dry_run=False,
            )

            self.assertAlmostEqual(duration, 1.0, places=3)

            updated = reaper_project.read_text(encoding="utf-8")
            self.assertIn(f'RENDER_FILE "{output_aif}"', updated)
            self.assertIn('RENDER_RANGE 4 0 0 0 1000', updated)
            self.assertIn('SELECTION 0 1', updated)
            self.assertIn('SELECTION2 0 1', updated)
            self.assertIn('LENGTH 1', updated)
            self.assertIn(f'FILE "{input_aiff}"', updated)


if __name__ == "__main__":
    unittest.main()