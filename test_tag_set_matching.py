from pathlib import Path
import unittest

from dj_set_prep_workflow.tag_set_mp3s import TrackEntry, score_candidate_mp3s


class TagSetMatchingTests(unittest.TestCase):
    def test_matches_title_with_ep_in_filename(self) -> None:
        entry = TrackEntry(
            title="Sea Of Souls (Extended Mix)",
            artist="Sebastian Sellares",
            label=None,
            year=None,
            filename=None,
        )
        mp3_files = [Path("Sebastian_Sellares-Sea_Of_Souls__EP_-Extended_Mix-79432091.mp3")]

        scored = score_candidate_mp3s(entry, mp3_files, used=set())

        self.assertEqual(scored[0][1], mp3_files[0])
        self.assertGreaterEqual(scored[0][0], 20)

    def test_matches_reversed_artist_order(self) -> None:
        entry = TrackEntry(
            title="Show Me (Main Mix)",
            artist="MissFly, Masaki Morii",
            label=None,
            year=None,
            filename=None,
        )
        mp3_files = [Path("Masaki_Morii__MissFly-Show_Me-Main_Mix-79783338.mp3")]

        scored = score_candidate_mp3s(entry, mp3_files, used=set())

        self.assertEqual(scored[0][1], mp3_files[0])
        self.assertGreaterEqual(scored[0][0], 20)

    def test_filename_hint_prioritizes_expected_candidate(self) -> None:
        entry = TrackEntry(
            title="Shared Title",
            artist="Shared Artist",
            label=None,
            year=None,
            filename="Exact_Target_File.mp3",
        )
        mp3_files = [
            Path("Shared_Artist-Shared_Title-12345.mp3"),
            Path("Exact_Target_File.mp3"),
        ]

        scored = score_candidate_mp3s(entry, mp3_files, used=set())

        self.assertEqual(scored[0][1], Path("Exact_Target_File.mp3"))


if __name__ == "__main__":
    unittest.main()
