from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dj_set_prep_workflow.tag_set_aiffs import parse_set_file as parse_aiff_set_file
from dj_set_prep_workflow.tag_set_mp3s import parse_set_file as parse_mp3_set_file


class MetadataParsingTests(unittest.TestCase):
    def test_parses_pipe_delimited_rows(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            set_file = Path(tmp_dir) / "raw-track-metadata.csv"
            set_file.write_text(
                "\n".join(
                    [
                        "Artist A|Track One [Bedrock 2024]|Artist A - Track One.mp3",
                        "Artist B|Track Two [2025]|track-two.aif",
                        "Artist C|Track Three|track-three.wav",
                    ]
                ),
                encoding="utf-8",
            )

            mp3_entries = parse_mp3_set_file(set_file)
            aiff_entries = parse_aiff_set_file(set_file)

            self.assertEqual(len(mp3_entries), 3)
            self.assertEqual(len(aiff_entries), 3)

            self.assertEqual(mp3_entries[0].artist, "Artist A")
            self.assertEqual(mp3_entries[0].title, "Track One")
            self.assertEqual(mp3_entries[0].label, "Bedrock")
            self.assertEqual(mp3_entries[0].year, "2024")
            self.assertEqual(mp3_entries[0].filename, "Artist A - Track One.mp3")

            self.assertEqual(mp3_entries[1].title, "Track Two")
            self.assertIsNone(mp3_entries[1].label)
            self.assertEqual(mp3_entries[1].year, "2025")

            self.assertEqual(mp3_entries[2].title, "Track Three")
            self.assertIsNone(mp3_entries[2].label)
            self.assertIsNone(mp3_entries[2].year)

    def test_rejects_malformed_row(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            set_file = Path(tmp_dir) / "raw-track-metadata.csv"
            set_file.write_text("Artist A|Track One [Bedrock 2024]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "pipe-delimited"):
                parse_mp3_set_file(set_file)

    def test_rejects_legacy_triplet_format(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            set_file = Path(tmp_dir) / "raw-track-metadata.txt"
            set_file.write_text(
                "\n".join(
                    [
                        "Track One",
                        "Artist A",
                        "[Bedrock 2024]",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "pipe-delimited"):
                parse_mp3_set_file(set_file)


if __name__ == "__main__":
    unittest.main()
