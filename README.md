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

`C:\Users\sherp\OneDrive\Music\ReleasePromo On OneDrive`

Global config variables in `src/dj_set_prep_workflow/tag_set_mp3s.py`:

- `YEAR = 2026`
- `MP3_SOURCE = C:\Users\sherp\OneDrive\Music\ReleasePromo On OneDrive`
- `INIT_TARGET_PATH = D:\MyMusic\Mixes\{YEAR} Mixes`

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

What it does:

1. Suggests a target set folder from `INIT_TARGET_PATH` (nearest future date folder) and lets you select.
2. Tags matched source MP3 files in `MP3_SOURCE` (no moving).
3. Converts matched tracks to 24-bit AIFF in `TARGET_PATH\AIFF` via ffmpeg.
4. Runs RX10 headless preset (`DJ Set Prep`) and writes output to `TARGET_PATH\aiffProcessed`.
5. Runs Essentia extractor (`streaming_extractor_music.exe <aiff> <json>`) on files in `aiffProcessed`.
6. Adds summarized Essentia info into AIFF comment tag on files in `aiffProcessed` (BPM, Camelot key, Camelot chords, energy). If key/chord metadata is present but unmapped, output uses `unknown`.
7. Generates `import_to_itunes.ps1` in target folder (pointing at `aiffProcessed`) and prompts manual iTunes import/playlist step.

If RX10 headless is not available yet, you can temporarily skip that stage and still test the rest of the flow:

```powershell
poetry run dj-flow --target-path "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --skip-rx10 --max-tracks 1 --no-interactive-unsure
```

Dry run:

```powershell
poetry run dj-flow --dry-run
```

Explicit target path:

```powershell
poetry run dj-flow --target-path "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --dry-run
```

Disable interactive unsure-match prompts:

```powershell
poetry run dj-flow --target-path "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --no-interactive-unsure
```

Custom genre:

```powershell
python tag_set_mp3s.py "D:\MyMusic\Mixes\2026 Mixes\2026.02.28" --default-genre "House"
```