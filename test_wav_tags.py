from pathlib import Path
import unittest

from mutagen.id3 import ID3, ID3NoHeaderError


class WavTagInspectionTests(unittest.TestCase):
    def test_can_read_id3_from_optional_fixture(self) -> None:
        fixture_path = Path(
            r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\ProcessedWAV\Azee_Project-Raise-Main_Mix-78594226.wav"
        )
        if not fixture_path.exists():
            self.skipTest(f"Fixture not available on this machine: {fixture_path}")

        try:
            tags = ID3(fixture_path)
        except ID3NoHeaderError:
            tags = None

        # This is an optional local inspection fixture; test just verifies we can read without crashing.
        self.assertTrue(tags is None or len(tags) >= 0)


if __name__ == "__main__":
    unittest.main()
