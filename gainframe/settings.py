"""GainFrame settings: a small dataclass persisted as JSON in the user's app-data dir."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODES = ("two-pass", "single-pass", "dynamic", "peak")


@dataclass
class Settings:
    # loudnorm targets
    target_i: float = -14.0      # integrated loudness, LUFS
    target_tp: float = -1.0      # true-peak ceiling, dBTP
    target_lra: float = 11.0     # loudness range, LU
    mode: str = "two-pass"       # two-pass | single-pass | dynamic | peak
    peak_target_db: float = -1.0  # for mode == "peak"
    # audio encoding
    audio_codec: str = "auto"    # auto = pick from output container
    audio_bitrate: str = "192k"
    # output
    container: str = "same"      # same | .mp4 | .mkv | ... (extension, with or without dot)
    output_dir: str = ""         # empty = same directory as the input file
    suffix: str = ".normalized"
    # tool locations (empty = auto-discover)
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    def normalized_container(self) -> str:
        c = (self.container or "same").strip().lower()
        if c in ("", "same"):
            return "same"
        return c if c.startswith(".") else "." + c

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        s = cls()
        for key, value in (data or {}).items():
            if hasattr(s, key):
                setattr(s, key, value)
        if s.mode not in MODES:
            s.mode = "two-pass"
        return s

    def to_dict(self) -> dict:
        return asdict(self)


# Presets: name -> Settings overrides
PRESETS = {
    "Streaming -14 LUFS": {"target_i": -14.0, "target_tp": -1.0, "target_lra": 11.0, "mode": "two-pass"},
    "Podcast -16 LUFS": {"target_i": -16.0, "target_tp": -1.0, "target_lra": 11.0, "mode": "two-pass"},
    "Broadcast -23 LUFS": {"target_i": -23.0, "target_tp": -1.0, "target_lra": 11.0, "mode": "two-pass"},
    "Peak -1 dB": {"mode": "peak", "peak_target_db": -1.0},
}
PRESET_CUSTOM = "Custom"


def settings_dir() -> Path:
    """%APPDATA%\\GainFrame on Windows; XDG config dir elsewhere."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "GainFrame"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "GainFrame"


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    p = path or settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return Settings.from_dict(data)
    except (OSError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    return p
