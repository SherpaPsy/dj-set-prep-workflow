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
├── ConvertedFiles
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

### Interactive menu

Command: `dj-menu`

```bash
poetry run dj-menu
```

Prompts for one of:

Items 1–3 are the weekly flow, in order:

1. **Prepare source files** — see below (also available as `dj-prep-sources`).
2. **Run clean** — same as `dj-flow --clean-start`.
3. **Import to Apple Music** — see below (also available as `dj-import-music`).
4. **Run** — process all source files, no cleanup first.
5. **Run clean (dry-run)** — preview a clean run without touching any files.
6. **Transcode check only** — runs just the transcode-detection scan below and prints its report; no conversion, rendering, or tagging.
7. **Quit**

All other options (source dir, metadata file, Reaper/ffmpeg paths, etc.) use `dj-flow`'s defaults. For anything beyond these, use `dj-flow` directly with flags.

### Prepare source files

Command: `dj-prep-sources` (or menu item 1)

0. **Preflight:** matches the promo files' tags against `tracklist.csv` before touching anything. If fewer than half match, it prints a loud warning that the two look like different sets (stale promo folder or tracklist) and aborts unless you type `continue`. `-y` never overrides this.
1. Shows what will happen and asks for confirmation (`-y` to skip), then **clears** `SourceFiles/`. Aborts without clearing if the promo folder has no audio.
2. Copies every `.mp3` / `.aif(f)` / `.flac` / `.m4a` from `~/Library/CloudStorage/OneDrive-Personal/Music/ReleasePromo On OneDrive` into `SourceFiles/`. `.wav` files are converted to `.aiff` (same bit depth, tags and artwork carried over). Non-audio files (e.g. `.jpg`) are skipped.
3. Matches each file to a row in `Metadata/tracklist.csv` (`Title,Artist` columns) and adds that row's `[Label Year]` to the file's title. The CSV comes from the pool web UI and is **release-level**: for EPs and remix packages its title is the release name (e.g. `Double Douze (EP)`), not the track's, and its artist list can include artists from other tracks on the release. So:
   - **Title written** = the file's own title, tidied (extra spaces collapsed, any old bracketed suffix dropped) + the CSV's `[Label Year]`. E.g. `Lift Him Up (Original Mix) [Natura Viva 2026]`.
   - **Artist written** = the file's own artist (the CSV artist is only used if the file has none).
   - **Matching** is fuzzy on artist, and compares the CSV title with both the file's title and its **album** tag, so EP rows match their tracks. Each CSV row is used at most once. Matches scoring under 0.85 are flagged `<-- CHECK`, files whose track title differs from the CSV row show that row for reference, and unmatched files/rows are listed (unmatched files keep their tags).

Flags: `--prep-root`, `--promo-dir`, `--tracklist`, `--ffmpeg-exe`, `-y/--yes`.

### Import to Apple Music

Command: `dj-import-music` (or menu item 3). macOS only; drives Music.app via AppleScript. The first run triggers a macOS prompt to let your terminal control Music; click **Allow** (System Settings → Privacy & Security → Automation).

Imports everything in `TaggedFiles/` into a **new** playlist named for the upcoming Saturday as `YYYY.MM.DD` (today, if today is Saturday). One batch is one playlist, so it checks before changing anything:

1. **Media folder reachable:** reads Music's media folder from its preferences (currently an external drive) and aborts if it isn't mounted.
2. **Playlist must not exist:** if `YYYY.MM.DD` already exists it warns loudly and stops, unless you type `resume` to finish an interrupted import. Resuming skips tracks already in the playlist, adds tracks already in the library without re-importing them, and imports the rest.
3. **Tracks not already in the library:** looks up each file's artist + title (ignoring spacing, case, punctuation). If any are found it warns and only continues if you type `continue`, in which case the existing library tracks are added to the playlist instead of re-importing.

It then creates the playlist and adds each file to the library, then finds it by artist + title and adds it to the playlist (Music doesn't always hand back a reference to a just-imported track). A track that fails is reported and the rest carry on; the summary tells you to re-run and `resume`. Afterwards it verifies every imported track lives inside the media folder, i.e. Music **copied** the file rather than linking to `TaggedFiles` (which the next run clears). If not, it warns you to turn on *Music → Settings → Files → Copy files to Music Media folder when adding to Library*.

Flags: `--dry-run` (all checks, no import), `--playlist NAME` (override the name), `--tagged-dir`, `--prep-root`, `-y/--yes`.

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