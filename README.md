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

`C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\SourceMP3s`

Global config variables in `src/dj_set_prep_workflow/tag_set_mp3s.py`:

- `YEAR = 2026`
- `MP3_SOURCE = C:\Users\sherp\OneDrive\Music\DJ-Set-Prep\SourceMP3s`
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
└── SourceMP3s
```

What it does:

1. Reads `Metadata/raw-track-metadata.txt` for title/artist/[label year] entries.
2. Matches each entry to one MP3 in `SourceMP3s`.
3. Processes tracks one-by-one:
	- reads existing MP3 tags into memory,
	- converts to 24-bit AIFF in `ConvertedAIFF`,
	- runs pre-master tool into `ProcessedAIFF` (or passthrough if skipped),
	- runs Essentia and writes JSON logs to `Logs`,
	- updates destination AIFF tags in `ProcessedAIFF`.
4. Writes full per-track metadata output to `Metadata/processed-track-metadata.txt`.
5. Stops before iTunes import/playlist steps.

If pre-master executable is not available yet, you can temporarily skip that stage and still test the rest of the flow:

```powershell
poetry run dj-flow --skip-premaster --max-tracks 1 --no-interactive-unsure
```

Dry run:

```powershell
poetry run dj-flow --dry-run
```

Use explicit root and tool paths:

```powershell
poetry run dj-flow --prep-root "C:\Users\sherp\OneDrive\Music\DJ-Set-Prep" --ffmpeg-exe "D:\AudioTools\ffmpeg\bin\ffmpeg.exe" --essentia-exe "D:\AudioTools\essentia-extractors-v2.1_beta2\streaming_extractor_music.exe" --dry-run
```

Disable interactive unsure-match prompts:

```powershell
poetry run dj-flow --no-interactive-unsure
```

Custom genre:

```powershell
python tag_set_mp3s.py "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --default-genre "House"
```