This is a python based workflow to prepare tracks for DJ sets.

## MP3 tagging from metadata file

Script: `tag_set_mp3s.py`

### Install

```bash
poetry install
```

### Expected set file format

Inside your set folder keep a metadata file with one row per track:

1. `artist|title [label year]|filename`

Example set folder path:
- **Mac:** `~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata`

Example rows:

```text
Sebastian Sellares|Sea Of Souls (Extended Mix) [Bedrock 2024]|Sebastian_Sellares-Sea_Of_Souls__EP_-Extended_Mix-79432091.mp3
MissFly, Masaki Morii|Show Me (Main Mix) [2026]|Masaki_Morii__MissFly-Show_Me-Main_Mix-79783338.mp3
```

### What the script does

- Uses metadata file as source.
- Finds and tags MP3 files from source library folder (recursive).
- Matches filenames using title and artist tokens, so common filename extras like `EP`, track IDs, or reordered artist names still resolve correctly.
- Uses the metadata title as the output title and appends `[label year]` when not already present.
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

`set_dir` is required. It must be the folder that contains your set metadata `.txt` or `.csv` file.

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

Specify metadata file explicitly (if it can't be auto-detected in `set_dir`):

**Mac:**
```bash
poetry run dj-tag ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata --set-file ~/Library/CloudStorage/OneDrive-Personal/Music/DJ-Set-Prep/Metadata/raw-track-metadata.csv
```

If you see `Set file rows must be pipe-delimited as artist|title [label year]|filename`, the selected file is not in the expected row format. Point `--set-file` to the correct metadata file.

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
│   ├── raw-track-metadata.csv
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
│   ├── raw-track-metadata.csv
│   └── processed-track-metadata.txt
├── ProcessedAIFF
├── Sourcefiles
└── Templates
```

What it does:

1. Lists source audio files from `Sourcefiles` (mp3/wav/aif/aiff/flac/m4a).
2. Runs an upfront transcode-detection scan across all discovered files (see below), prints a report to stdout, and pauses for you to review it before continuing.
3. Processes each source file one-by-one.
4. For each file:
	- extracts existing tags into a dictionary (including path/name/stem),
	- converts to 24-bit AIFF in `ConvertedAIFF`,
	- copies converted file to `Templates/input.aiff`,
	- runs Reaper render project (`Templates/DJ Set Prep.rpp`),
	- renames `ProcessedAIFF/output.aif` to `ProcessedAIFF/<filename>.aif`,
	- runs Essentia and writes JSON/logs to `Logs`,
  - rewrites destination tags on the rendered AIFF (title append from metadata line 3, Essentia comment, album artist, year/genre),
  - copies the final tagged AIFF to `TaggedFiles/<filename>.aif`.
5. Writes full per-track output to `Metadata/processed-track-metadata.txt`.

### Transcode detection scan

Before any conversion or rendering happens, the flow runs every source file through [bitrater](https://github.com/yamsnjams/bitrater) — a pre-trained CNN+BiLSTM model that classifies the true encoding quality of an audio file from its spectral content (128/192/256/320 kbps CBR, V0/V2 VBR, or genuinely lossless) and flags a mismatch against what the file claims to be. This catches files that claim to be high bitrate (or are lossless WAV/AIFF/FLAC) but were actually upsampled/transcoded from a lower-bitrate lossy source.

- Report columns: stated class (from file tags/container), detected class (from audio content), model confidence, and a verdict:
  - `OK` — detected quality is consistent with the stated class.
  - `LIKELY_TRANSCODED` — detected quality is meaningfully lower than the stated class (e.g. a "320kbps"/lossless file whose content matches a 128kbps source).
  - `UNCERTAIN` — not flagged as a transcode, but the model's confidence was low; worth a manual listen.
  - `ERROR` — the file couldn't be decoded/classified.
- The bundled model claims ~98% accuracy per its own benchmarks, but this is still a classifier, not a certainty — treat flagged files as "worth a second listen," not confirmed fakes.
- The bitrater model is loaded once per run (a one-time cost of several seconds), then reused for every file in the scan.
- The flow pauses after printing the report so you can review it before processing continues. Press Enter to proceed.
- Skip this step entirely with `--skip-transcode-check`.

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
poetry run dj-flow --clean-start
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