---
name: screencap-tracklist
description: Convert a screenshot of the record-pool downloads page (TRACK / ARTIST / LABEL columns) into Metadata/tracklist.csv for the prepare-source-files step. Use when the user says "convert this screencap to tracklist.csv", "make the tracklist from this screenshot", or pastes a pool screenshot and asks for the tracklist.
---

# Screenshot → tracklist.csv

The user pastes one or more screenshots of their pool's weekly downloads page. Each row shows:
**Track title** (bold) + **mix name** (grey), **Artist(s)**, **Label**.

## Steps

1. **Read every row** from the screenshot(s), top to bottom, in page order. If there are several screenshots, merge them and drop duplicate rows where they overlap.
2. **Never guess.** If a label, title or artist is cut off ("…"), blurry or ambiguous, stop and ask the user. Don't write a best guess into the file. Wrong labels end up permanently in the audio tags.
3. **Year:** the page has no year column. Use the current year unless the user gives another one or a row shows a year. Say which year you used.
4. **Back up** the existing file before overwriting it:
   `cp ~/Music/DJ-Set-Prep/Metadata/tracklist.csv <scratchpad>/tracklist-<old file's mtime date>.bak.csv`
5. **Write** `~/Music/DJ-Set-Prep/Metadata/tracklist.csv` (the prep root comes from `dj_set_prep_workflow.paths.resolve_default_prep_root()`; check that it's still `~/Music/DJ-Set-Prep`). Use Python's `csv` module (UTF-8, `\n` line endings) so fields with commas get quoted:

   ```
   Title,Artist
   Face The Flow (Original Mix) [Ensis Music 2026],Jacob Maess
   Knowledge (EP) (Original Mix) [Music is 4 Lovers 2026],"Maytee M, Suengas, Lejos"
   ```

   - Title = `<track title> (<mix name>) [<Label> <Year>]`. Keep the title exactly as shown, including `(EP)`, `(Remixes)`, `?` etc.
   - Artist = the artist column exactly as shown (comma-separated list, `&` kept).
   - Keep label capitalisation exactly as shown (`ILINX`, `Music is 4 Lovers`).
6. **Show** the written file and report: row count, year used, backup path, and anything odd (e.g. a title that already contains a year).
7. **Verify before download:** the pool page disappears once the user downloads, so ask them to compare the written rows with the live page (row count, labels) *before* downloading. Also tell them to keep the screenshot.
8. Remind the user to put downloads in `~/Library/CloudStorage/OneDrive-Personal/Music/ReleasePromo On OneDrive` (not SourceFiles, which "Prepare source files" clears). Also remind them that "Prepare source files" preflight will warn if ReleasePromo still holds last week's tracks.
