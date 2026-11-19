from __future__ import annotations

import aifc
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mutagen.aiff import AIFF
from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2

from dj_set_prep_workflow.prepare_source_files import (
    CsvTrack,
    FileTags,
    plan_tags,
    read_tags,
    track_name,
    write_tags,
    year_from_csv_title,
)


def _create_test_aiff(path: Path) -> None:
    with aifc.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(2)
        audio_file.setsampwidth(2)
        audio_file.setframerate(44100)
        audio_file.writeframes(b"\x00\x00\x00\x00" * 32)


class CsvTitleParsingTests(unittest.TestCase):
    def test_year_from_csv_title(self) -> None:
        self.assertEqual(year_from_csv_title("Face The Flow (Original Mix) [Ensis Music 2026]"), "2026")
        self.assertEqual(year_from_csv_title("Where Are You Now? 2026 (2026 Version) [Best Works 2025]"), "2025")
        self.assertEqual(year_from_csv_title("No Suffix (Original Mix)"), "")

    def test_track_name(self) -> None:
        self.assertEqual(track_name("Lift Him Up (Original Mix) [Natura Viva 2026]"), "Lift Him Up")
        self.assertEqual(track_name("Ark 47 Libra (Mike Kohl Extended Remix) [Anoka 2026]"), "Ark 47 Libra")
        self.assertEqual(track_name("Help (Extended Mix) [GDB Records 2026]"), "Help")
        self.assertEqual(track_name("Where Are You Now? 2026 (2026 Version) [Best Works 2026]"), "Where Are You Now? 2026")


class PlanTagsTests(unittest.TestCase):
    track = CsvTrack(title="Knowledge (EP) (Original Mix) [Music is 4 Lovers 2026]", artist="Maytee M, Suengas, Lejos")

    def test_fills_missing_year_album_artist_album(self) -> None:
        # EP track: album is the track's own name, not the EP/release name.
        planned = plan_tags(FileTags(title="Music For Me (Original Mix)", artist="Maytee M", album=""), self.track)
        self.assertEqual(
            planned,
            {
                "title": "Music For Me (Original Mix) [Music is 4 Lovers 2026]",
                "artist": "Maytee M",
                "year": "2026",
                "album_artist": "Maytee M",
                "album": "Music For Me",
            },
        )

    def test_keeps_existing_year_album_artist_album(self) -> None:
        tags = FileTags(title="Knowledge", artist="Maytee M", album="Knowledge EP", year="2025", album_artist="Various")
        planned = plan_tags(tags, self.track)
        self.assertEqual(set(planned), {"title", "artist"})


class WriteTagsTests(unittest.TestCase):
    values = {
        "title": "Seaweed (Original Mix) [Rynth 2026]",
        "artist": "Tim Otion",
        "year": "2026",
        "album_artist": "Tim Otion",
        "album": "Seaweed",
    }

    def test_round_trip_aiff(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "track.aif"
            _create_test_aiff(path)
            write_tags(path, self.values)

            tags = AIFF(path).tags
            self.assertEqual(str(tags["TIT2"]), self.values["title"])
            self.assertEqual(str(tags["TDRC"]), "2026")
            self.assertEqual(str(tags["TPE2"]), "Tim Otion")
            self.assertEqual(str(tags["TALB"]), "Seaweed")
            self.assertEqual(
                read_tags(path),
                FileTags(self.values["title"], "Tim Otion", "Seaweed", "2026", "Tim Otion"),
            )

    def test_mp3_only_overwrites_given_frames(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "track.mp3"
            path.write_bytes(b"")
            tags = ID3()
            tags.add(TIT2(encoding=3, text="Seaweed (Original Mix)"))
            tags.add(TPE1(encoding=3, text="Tim Otion"))
            tags.add(TALB(encoding=3, text="Existing Album"))
            tags.add(TDRC(encoding=3, text="2025"))
            tags.add(TPE2(encoding=3, text="Existing AA"))
            tags.save(path)

            write_tags(path, {"title": self.values["title"], "artist": "Tim Otion"})

            saved = ID3(path)
            self.assertEqual(str(saved["TIT2"]), self.values["title"])
            self.assertEqual(str(saved["TALB"]), "Existing Album")
            self.assertEqual(str(saved["TDRC"]), "2025")
            self.assertEqual(str(saved["TPE2"]), "Existing AA")


if __name__ == "__main__":
    unittest.main()
