This is a python based workflow to prepare tracks for DJ sets.

## MP3 tagging from set text file

Script: `tag_set_mp3s.py`

### Install

```bash
poetry install
```

### Expected set file format

Inside your set folder keep a text file with blocks of:

1. `title (version)`
2. `artist`
3. `[label year]`

Example set folder path:
- **Mac:** `~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata`

Optional separator line every few tracks:

```text
====================
```

### What the script does

- Uses set text file as source.
- Finds and tags MP3 files from source library folder (recursive).
- Matches filenames using title and artist tokens, so common filename extras like `EP`, track IDs, or reordered artist names still resolve correctly.
- Uses the set-file title as the output title and appends `[label year]` when not already present.
- Sets `Artist` and `Album Artist` from the set file.
- Sets `Year` only when missing.
- Clears all comments.
- Adds genre when missing.
- Prompts interactively for uncertain filename matches (disable with `--no-interactive-unsure`).

Default source MP3 folder:

- **Mac:** `~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/SourceFiles`
- **Windows:** `C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Sourcefiles`

Global config variables in `src/dj_set_prep_workflow/tag_set_mp3s.py`:

- `YEAR = 2026`
- `MP3_SOURCE` — path to source MP3 folder (see above for platform examples)
- `INIT_TARGET_PATH` — path to metadata folder (e.g. `~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata` on Mac, `C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Metadata` on Windows)

### Run

`set_dir` is required. It must be the folder that contains your set `.txt` file.

If your set file is in:

`~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata`

you can run either from inside that folder (using `.`) or by passing the folder path.

Dry run first:

**Mac (from inside the set folder):**
```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata
poetry run dj-tag . --dry-run
```

**Mac (pass folder path directly):**
```bash
poetry run dj-tag ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --dry-run
```

Write tags:

**Mac:**
```bash
poetry run dj-tag ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata
```

Disable interactive unsure-match prompts:

**Mac:**
```bash
poetry run dj-tag ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --no-interactive-unsure
```

Specify set text file explicitly (if it can't be auto-detected in `set_dir`):

**Mac:**
```bash
poetry run dj-tag ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --set-file ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata/raw-track-metadata.txt
```

If you see `Set file should have title/artist/[label year] triplets`, the selected text file is not in the expected 3-line repeating format. Point `--set-file` to the correct set list file.

### Backward compatible script call

**Mac:**
```bash
python tag_set_mp3s.py ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --dry-run
```

**Windows (PowerShell):**
```powershell
python tag_set_mp3s.py "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Metadata" --dry-run
```

## High-level set prep flow

Command: `dj-flow`

Expected root directory structure:

**Mac** (`~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep`):
```text
~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep
├── Artwork
├── ConvertedFiles
├── Coverart
├── Logs
├── Metadata
│   ├── raw-track-metadata.txt
│   └── processed-track-metadata.txt
├── ProcessedFiles
├── SourceFiles
├── TaggedFiles
└── Templates
```

**Windows** (`C:\Users\sherp\OneDrive\Music\DJ-Set-Prep`):
```text
C:\Users\sherp\OneDrive\Music\DJ-Set-Prep
├── Artwork
├── ConvertedAIFF
├── Logs
├── Metadata
│   ├── raw-track-metadata.txt
│   └── processed-track-metadata.txt
├── ProcessedAIFF
├── Sourcefiles
└── Templates
```

What it does:

1. Lists source audio files from `Sourcefiles` (mp3/wav/aif/aiff/flac/m4a).
2. Processes each source file one-by-one.
3. For each file:
	- extracts existing tags into a dictionary (including path/name/stem),
	- converts to 24-bit AIFF in `ConvertedAIFF`,
	- copies converted file to `Templates/input.aiff`,
	- runs Reaper render project (`Templates/DJ Set Prep.rpp`),
	- renames `ProcessedAIFF/output.aif` to `ProcessedAIFF/<filename>.aif`,
	- runs Essentia and writes JSON/logs to `Logs`,
  - rewrites destination tags on the rendered AIFF (title append from metadata line 3, Essentia comment, album artist, year/genre),
  - copies the final tagged AIFF to `TaggedFiles/<filename>.aif`.
4. Writes full per-track output to `Metadata/processed-track-metadata.txt`.

Optional cleanup:

- Use `--clean-start` to clear `ConvertedFiles`, `ProcessedFiles`, and `TaggedFiles` before processing.
- `Logs` and `Metadata` are not deleted by `--clean-start`.

Overwrite behavior:

- `Metadata/processed-track-metadata.txt` is overwritten each run.
- Per-track logs and Essentia JSON files in `Logs` are overwritten when the same track stem is processed again.
- Older unrelated files in `Logs` remain unless you delete them manually.

You can enable optional interactive pauses after each stage in dry or non-dry mode:

**Mac:**
```bash
poetry run dj-flow --confirm-steps --dry-run
```

**Windows (PowerShell):**
```powershell
poetry run dj-flow --confirm-steps --dry-run
```

Run a one-track dry-run with explicit tools:

**Mac:**
```bash
poetry run dj-flow \
  --prep-root "~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep" \
  --ffmpeg-exe "/opt/homebrew/bin/ffmpeg" \
  --reaper-exe "/Applications/REAPER.app/Contents/MacOS/reaper" \
  --essentia-exe "/path/to/essentia/streaming_extractor_music" \
  --max-tracks 1 --clean-start --dry-run
```

**Windows (PowerShell):**
```powershell
poetry run dj-flow `
  --prep-root "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep" `
  --ffmpeg-exe "D:\AudioTools\ffmpeg\bin\ffmpeg.exe" `
  --reaper-exe "C:\Program Files\REAPER (x64)\reaper.exe" `
  --essentia-exe "D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe" `
  --max-tracks 1 --dry-run
```

Run real processing:

**Mac:**
```bash
poetry run dj-flow \
  --prep-root "~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep" \
  --ffmpeg-exe "/opt/homebrew/bin/ffmpeg" \
  --reaper-exe "/Applications/REAPER.app/Contents/MacOS/reaper" \
  --essentia-exe "/path/to/essentia/streaming_extractor_music"
```

**Windows (PowerShell):**
```powershell
poetry run dj-flow `
  --prep-root "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep" `
  --ffmpeg-exe "D:\AudioTools\ffmpeg\bin\ffmpeg.exe" `
  --reaper-exe "C:\Program Files\REAPER (x64)\reaper.exe" `
  --essentia-exe "D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
```

Custom genre:

**Mac:**
```bash
python tag_set_mp3s.py ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --default-genre "House"
```

**Windows (PowerShell):**
```powershell
python tag_set_mp3s.py "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Metadata" --default-genre "House"
```