# GainFrame

A tiny Windows app that does exactly one thing: **normalize the audio of a
video file** — and leave everything else alone.

It exists because the usual gain tools kept either mangling files, stripping
the video track, or arriving in installers of dubious cleanliness. GainFrame
is a clean-room replacement: a small Python/tkinter front-end over ffmpeg,
with the whole pipeline visible in the log pane.

**Product page & download:** [gainframe.cc](https://gainframe.cc)

## The one promise

The video stream is **stream-copied bit-for-bit** (`-c:v copy`). It is never
decoded, never re-encoded, never resized. Same for subtitles, chapters and
metadata. Only the audio tracks are decoded, loudness-normalized and
re-encoded. Every audio track is normalized.

## Quick start

1. Download `GainFrame-windows.zip` from Releases (or a CI artifact), unzip.
2. Run `GainFrame.exe` (ffmpeg.exe/ffprobe.exe sit next to it in the zip).
3. Add files, pick a preset, hit **Normalize**.

Output defaults to `<name>.normalized<same extension>` next to the input.

## Defaults & presets

| Preset | Target | Notes |
|---|---|---|
| Streaming −14 LUFS | −14 LUFS, −1.0 dBTP, LRA 11 | default; YouTube/Spotify-style |
| Podcast −16 LUFS | −16 LUFS, −1.0 dBTP, LRA 11 | spoken-word standard |
| Broadcast −23 LUFS | −23 LUFS, −1.0 dBTP, LRA 11 | EBU R128 territory |
| Peak −1 dB | max sample peak −1.0 dB | classic peak normalize |
| Custom | whatever is in Settings | |

The default quality path is **two-pass loudnorm**: pass 1 measures the file,
pass 2 applies the measured values with linear scaling when the targets are
reachable that way, and falls back to single-pass loudnorm when they aren't.

## Settings (the power-user layer)

- **Target loudness / true-peak / LRA** — the loudnorm targets.
- **Mode** — `two-pass` (default), `single-pass` loudnorm, `dynamic`
  (ffmpeg `dynaudnorm`), or `peak` (measure max peak with `volumedetect`,
  apply the exact gain to hit the peak target).
- **Audio codec** — `auto` picks from the output container
  (mp4/mov/m4v/mkv → AAC, webm → Opus, avi → MP3); override with any ffmpeg
  encoder name.
- **Audio bitrate** — default `192k`.
- **Container** — `same` as input, or an extension like `mkv` (the encoder
  map adjusts).
- **ffmpeg / ffprobe path overrides** — GainFrame looks for ffmpeg in this
  order: next to the exe, then the Settings override, then your PATH. If none
  is found you get a dialog saying exactly that.

Settings persist to `%APPDATA%\GainFrame\settings.json`.

## Notes

- Inputs: anything ffmpeg can read — mp4, mkv, mov, webm, avi, and more.
- Drag-and-drop is intentionally not included: doing it without extra
  dependencies isn't possible with stdlib tkinter, and GainFrame's rule is
  zero fragile dependencies. Use **Add Files...** (multi-select works).
- The GUI and the engine are separate: `gainframe/core.py` runs headless via
  `python -m gainframe.cli input.mp4 --mode two-pass` (add `--measure` to
  just print a file's integrated loudness).

## Building

- App: Python 3.10+, stdlib only. `python gainframe_app.py`
- Executable: `pip install pyinstaller && pyinstaller GainFrame.spec`
- CI: `.github/workflows/build.yml` builds on `windows-latest`, bundles a
  pinned static ffmpeg (gyan.dev essentials build), and uploads a zip
  artifact; tag pushes (`v*`) also attach the zip to a GitHub release.
- Tests: `python tests/run_tests.py` generates quiet/loud test files with
  ffmpeg and asserts output loudness, bit-identical video streams, and
  peak-mode accuracy.
