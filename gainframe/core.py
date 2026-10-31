"""GainFrame core pipeline.

The one promise: the video stream is ALWAYS stream-copied (`-c:v copy`),
never re-encoded. Only the audio is decoded, normalized and re-encoded.
Everything here is GUI-free so the pipeline can be driven from the CLI
or from tests.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .settings import Settings

AUDIO_CODEC_BY_EXT = {
    ".mp4": "aac", ".mov": "aac", ".m4v": "aac", ".m4a": "aac",
    ".mkv": "aac", ".matroska": "aac",
    ".webm": "libopus",
    ".avi": "libmp3lame", ".mp3": "libmp3lame",
    ".ts": "aac", ".flv": "aac", ".wmv": "aac",
}

# loudnorm internally works at 192 kHz; resample the result to a sane rate.
POST_RESAMPLE = "aresample=48000"


class GainFrameError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# tool discovery
# --------------------------------------------------------------------------

def app_dir() -> Path:
    """Directory of the running executable (PyInstaller) or of the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_binary(name: str, override: str = "") -> str | None:
    """Order: bundled next to the app, then the settings override, then PATH."""
    exe_name = name + (".exe" if sys.platform.startswith("win") else "")
    bundled = app_dir() / exe_name
    if bundled.is_file():
        return str(bundled)
    if override and Path(override).is_file():
        return override
    return shutil.which(name)


def resolve_tools(settings: Settings) -> tuple[str, str]:
    ffmpeg = find_binary("ffmpeg", settings.ffmpeg_path)
    ffprobe = find_binary("ffprobe", settings.ffprobe_path)
    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
    if missing:
        raise GainFrameError(
            "Could not find: " + ", ".join(missing) + ".\n\n"
            "GainFrame needs ffmpeg and ffprobe. Fix it one of these ways:\n"
            "  1. Put ffmpeg.exe and ffprobe.exe in the same folder as GainFrame.exe, or\n"
            "  2. Set their paths in Settings, or\n"
            "  3. Install ffmpeg so it is on your PATH."
        )
    return ffmpeg, ffprobe


# --------------------------------------------------------------------------
# probing and measurement
# --------------------------------------------------------------------------

def probe(ffprobe: str, path: Path) -> dict:
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise GainFrameError(f"ffprobe failed on {path}:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def audio_stream_indexes(info: dict) -> list[int]:
    """Relative audio indexes (0:a:0, 0:a:1, ...) in file order."""
    idxs = []
    n = 0
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            idxs.append(n)
            n += 1
    return idxs


def duration_of(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        return 0.0


_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}")


def measure_loudnorm(ffmpeg: str, path: Path, a_idx: int, settings: Settings,
                     log_cb=None) -> dict | None:
    """Run loudnorm analysis on one audio stream; return the measured_* dict or None."""
    flt = (f"loudnorm=I={settings.target_i}:TP={settings.target_tp}:"
           f"LRA={settings.target_lra}:print_format=json")
    cmd = [ffmpeg, "-hide_banner", "-i", str(path),
           "-map", f"0:a:{a_idx}", "-af", flt, "-f", "null", "-"]
    if log_cb:
        log_cb("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    blocks = _JSON_BLOCK.findall(r.stderr)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except ValueError:
        return None


_MAX_VOLUME = re.compile(r"max_volume:\s*(-inf|-?\d+(?:\.\d+)?)\s*dB")


def measure_peak_db(ffmpeg: str, path: Path, a_idx: int, log_cb=None) -> float | None:
    """Max sample peak (dBFS) of one audio stream via volumedetect. None = silent / unknown."""
    cmd = [ffmpeg, "-hide_banner", "-i", str(path),
           "-map", f"0:a:{a_idx}", "-af", "volumedetect", "-f", "null", "-"]
    if log_cb:
        log_cb("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = _MAX_VOLUME.search(r.stderr)
    if not m or m.group(1) == "-inf":
        return None
    return float(m.group(1))


def integrated_lufs(ffmpeg: str, path: Path, a_idx: int = 0,
                    settings: Settings | None = None) -> float | None:
    """Convenience for tests/diagnostics: integrated loudness of a stream."""
    measured = measure_loudnorm(ffmpeg, path, a_idx, settings or Settings())
    if not measured:
        return None
    try:
        return float(measured["input_i"])
    except (KeyError, ValueError):
        return None


# --------------------------------------------------------------------------
# filter construction
# --------------------------------------------------------------------------

def _fmt(x: float) -> str:
    return f"{x:g}"


def loudnorm_filter(settings: Settings, measured: dict | None) -> str:
    """loudnorm filter for one stream. Uses measured values + linear mode when
    linear normalization can actually reach the targets; otherwise single-pass
    (dynamic) loudnorm."""
    base = f"I={_fmt(settings.target_i)}:TP={_fmt(settings.target_tp)}:LRA={_fmt(settings.target_lra)}"
    if measured:
        try:
            m_i = float(measured["input_i"])
            m_tp = float(measured["input_tp"])
            m_lra = float(measured["input_lra"])
            m_thresh = float(measured["input_thresh"])
            offset = float(measured["target_offset"])
        except (KeyError, ValueError):
            return f"loudnorm={base},{POST_RESAMPLE}"
        gain = settings.target_i - m_i
        linear_possible = (m_tp + gain) <= settings.target_tp + 1e-9
        if linear_possible:
            return (
                f"loudnorm={base}:measured_I={_fmt(m_i)}:measured_TP={_fmt(m_tp)}:"
                f"measured_LRA={_fmt(m_lra)}:measured_thresh={_fmt(m_thresh)}:"
                f"offset={_fmt(offset)}:linear=true,{POST_RESAMPLE}"
            )
    return f"loudnorm={base},{POST_RESAMPLE}"


def build_stream_filter(ffmpeg: str, path: Path, a_idx: int, settings: Settings,
                        log_cb=None) -> tuple[str, dict | None, str]:
    """Return (filter_string, measured_dict_or_None, note) for one audio stream."""
    if settings.mode == "two-pass":
        measured = measure_loudnorm(ffmpeg, path, a_idx, settings, log_cb=log_cb)
        if measured is None:
            return loudnorm_filter(settings, None), None, "measure failed -> single-pass loudnorm"
        flt = loudnorm_filter(settings, measured)
        note = "two-pass loudnorm (linear)" if "linear=true" in flt else \
               "two-pass measured, linear not possible -> single-pass loudnorm"
        return flt, measured, note
    if settings.mode == "single-pass":
        return loudnorm_filter(settings, None), None, "single-pass loudnorm"
    if settings.mode == "dynamic":
        return "dynaudnorm=f=500:g=31:p=0.95:m=10", None, "dynaudnorm"
    if settings.mode == "peak":
        peak = measure_peak_db(ffmpeg, path, a_idx, log_cb=log_cb)
        if peak is None:
            return "volume=0dB", None, "peak: stream silent/unmeasurable -> left unchanged"
        gain = settings.peak_target_db - peak
        return f"volume={gain:.2f}dB", {"max_volume": peak, "gain_db": gain}, \
               f"peak normalize ({peak:.2f} dB -> {settings.peak_target_db:.2f} dB)"
    raise GainFrameError(f"unknown mode: {settings.mode}")


# --------------------------------------------------------------------------
# output naming / codec choice
# --------------------------------------------------------------------------

def output_path_for(input_path: Path, settings: Settings) -> Path:
    cont = settings.normalized_container()
    ext = input_path.suffix if cont == "same" else cont
    out_dir = Path(settings.output_dir) if settings.output_dir else input_path.parent
    return out_dir / f"{input_path.stem}{settings.suffix}{ext}"


def audio_codec_for(output_path: Path, settings: Settings) -> str:
    if settings.audio_codec and settings.audio_codec != "auto":
        return settings.audio_codec
    return AUDIO_CODEC_BY_EXT.get(output_path.suffix.lower(), "aac")


# --------------------------------------------------------------------------
# ffmpeg execution
# --------------------------------------------------------------------------

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _run_encode(cmd: list[str], duration: float, progress_cb=None, log_cb=None) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    tail: list[str] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        if log_cb:
            log_cb("  " + line.rstrip())
        m = _TIME_RE.search(line)
        if m and duration > 0 and progress_cb:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            progress_cb(min(t / duration, 1.0))
    rc = proc.wait()
    return rc, "".join(tail)


def build_command(ffmpeg: str, input_path: Path, output_path: Path,
                  settings: Settings, filters: list[str]) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(input_path),
           "-map", "0",
           "-c", "copy",                       # everything stream-copied by default...
           "-c:a", audio_codec_for(output_path, settings),  # ...except audio
           "-b:a", settings.audio_bitrate]
    for a_idx, flt in enumerate(filters):
        cmd += [f"-filter:a:{a_idx}", flt]
    cmd += ["-c:v", "copy",                    # stated twice on purpose: never re-encode video
            "-map_metadata", "0", "-map_chapters", "0",
            str(output_path)]
    return cmd


def normalize_file(input_path: str | Path, settings: Settings,
                   output_path: str | Path | None = None,
                   progress_cb=None, phase_cb=None, log_cb=None) -> dict:
    """Normalize one file. Returns a result dict; raises GainFrameError on failure."""
    input_path = Path(input_path)
    ffmpeg, ffprobe = resolve_tools(settings)
    info = probe(ffprobe, input_path)
    a_idxs = audio_stream_indexes(info)
    if not a_idxs:
        raise GainFrameError(f"{input_path.name}: no audio streams found - nothing to normalize.")
    duration = duration_of(info)
    out = Path(output_path) if output_path else output_path_for(input_path, settings)
    out.parent.mkdir(parents=True, exist_ok=True)

    if phase_cb:
        phase_cb("analyzing")
    filters: list[str] = []
    measured_all: dict[int, dict | None] = {}
    notes: list[str] = []
    for a_idx in a_idxs:
        flt, measured, note = build_stream_filter(ffmpeg, input_path, a_idx, settings, log_cb=log_cb)
        filters.append(flt)
        measured_all[a_idx] = measured
        notes.append(f"audio {a_idx}: {note}")

    cmd = build_command(ffmpeg, input_path, out, settings, filters)
    if log_cb:
        log_cb("$ " + " ".join(cmd))
    if phase_cb:
        phase_cb("normalizing")
    rc, tail = _run_encode(cmd, duration, progress_cb=progress_cb, log_cb=log_cb)
    if rc != 0:
        raise GainFrameError(f"ffmpeg failed on {input_path.name} (exit {rc}):\n{tail.strip()}")
    if progress_cb:
        progress_cb(1.0)
    return {
        "input": str(input_path),
        "output": str(out),
        "duration": duration,
        "mode": settings.mode,
        "measured": {k: v for k, v in measured_all.items()},
        "notes": notes,
        "command": cmd,
    }
