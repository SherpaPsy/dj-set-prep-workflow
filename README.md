This is a python based workflow to prepare tracks for DJ sets.

## MP3 tagging from set text file

Script: `tag_set_mp3s.py`

### Install

```powershell
poetry install
```

### Expected set file format

Inside your set folder (example: `D:\MyMusic\Mixes\2026 Mixes\2026.02.28\`) keep a text file with blocks of:

1. `title (version)`
2. `artist`
3. `[label year]`

Optional separator line every few tracks:

```text
====================
```

### What the script does

- Uses set text file as source.
- Finds and tags MP3 files from source library folder (recursive).
- Preserves existing `Title` tag by default and only appends `[label year]` when not already present.
- Falls back to set-file title, then filename stem if `Title` tag is missing.
- Sets `Artist` and `Album Artist` to source artist.
- Sets `Year` only when missing.
- Clears all comments.
- Adds genre when missing.
- Prompts interactively for uncertain filename matches (disable with `--no-interactive-unsure`).

Default source MP3 folder:

`C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Sourcefiles`

Global config variables in `src/dj_set_prep_workflow/tag_set_mp3s.py`:

- `YEAR = 2026`
- `MP3_SOURCE = C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Sourcefiles`
- `INIT_TARGET_PATH = C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\Metadata`

### Run

Dry run first:

```powershell
poetry run dj-tag "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --dry-run
```

Write tags:

```powershell
poetry run dj-tag "D:\MyMusic\Mixes\2026 Mixes\2026.02.28"
```

Override source MP3 folder:

```powershell
poetry run dj-tag "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --source-dir "D:\Other\Music\Source"
```

Disable interactive unsure-match prompts:

```powershell
poetry run dj-tag "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --no-interactive-unsure
```

### Backward compatible script call

You can still run:

```powershell
python tag_set_mp3s.py "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --dry-run
```

## High-level set prep flow

Command: `dj-flow`

Expected root directory structure:

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
	- updates destination tags on rendered AIFF (title append from metadata line 3, Essentia comment, album artist, year/genre).
4. Writes full per-track output to `Metadata/processed-track-metadata.txt`.

You can enable optional interactive pauses after each stage in dry or non-dry mode:

```powershell
poetry run dj-flow --confirm-steps --dry-run
```

Run a one-track dry-run with explicit tools:

```powershell
poetry run dj-flow --prep-root "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep" --ffmpeg-exe "D:\AudioTools\ffmpeg\bin\ffmpeg.exe" --reaper-exe "C:\Program Files\REAPER (x64)\reaper.exe" --essentia-exe "D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe" --max-tracks 1 --dry-run
```

Run real processing:

```powershell
poetry run dj-flow --prep-root "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep" --ffmpeg-exe "D:\AudioTools\ffmpeg\bin\ffmpeg.exe" --reaper-exe "C:\Program Files\REAPER (x64)\reaper.exe" --essentia-exe "D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe"
```

Custom genre:

```powershell
python tag_set_mp3s.py "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --default-genre "House"
```