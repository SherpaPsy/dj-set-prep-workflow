from __future__ import annotations

import json
from pathlib import Path

from mutagen._riff import RiffFile


def main() -> None:
    path = Path(
        r"C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\ProcessedWAV"
        r"\Azee_Project-Raise-Main_Mix-78594226.wav"
    )
    with path.open("rb") as fileobj:
        riff = RiffFile(fileobj)
        info = None
        for chunk in riff.root.subchunks():
            if chunk.id == "LIST" and getattr(chunk, "name", None) == "INFO":
                info = chunk
                break

        if info is None:
            print("INFO list not found")
            return

        out: dict[str, str] = {}
        for key in ("IART", "INAM", "IPRD", "IGNR", "ICRD", "ICMT"):
            if key in info:
                out[key] = info[key].read().decode("utf-8", "ignore").rstrip("\x00")

        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
