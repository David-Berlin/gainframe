"""Headless CLI for GainFrame: python -m gainframe.cli FILE... [options]

The GUI is a thin shell over gainframe.core; this CLI drives the same code,
which is also how the test-suite exercises the pipeline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core
from .settings import MODES, Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gainframe", description="Normalize the audio of video files (video is stream-copied, never re-encoded).")
    p.add_argument("inputs", nargs="+", help="input media files")
    p.add_argument("--mode", choices=MODES, default=None)
    p.add_argument("--i", type=float, default=None, help="target integrated loudness, LUFS")
    p.add_argument("--tp", type=float, default=None, help="true-peak ceiling, dBTP")
    p.add_argument("--lra", type=float, default=None, help="target loudness range, LU")
    p.add_argument("--peak", type=float, default=None, help="peak target dB (mode=peak)")
    p.add_argument("--codec", default=None, help="audio codec override (default: auto by container)")
    p.add_argument("--bitrate", default=None, help="audio bitrate, e.g. 192k")
    p.add_argument("--container", default=None, help="output container extension or 'same'")
    p.add_argument("--out-dir", default=None, help="output directory (default: next to input)")
    p.add_argument("--suffix", default=None, help="output filename suffix")
    p.add_argument("--ffmpeg", default=None)
    p.add_argument("--ffprobe", default=None)
    p.add_argument("--measure", action="store_true",
                   help="only measure and print each file's integrated loudness, then exit")
    return p


def settings_from_args(args) -> Settings:
    s = Settings()
    if args.mode is not None:
        s.mode = args.mode
    if args.i is not None:
        s.target_i = args.i
    if args.tp is not None:
        s.target_tp = args.tp
    if args.lra is not None:
        s.target_lra = args.lra
    if args.peak is not None:
        s.peak_target_db = args.peak
    if args.codec is not None:
        s.audio_codec = args.codec
    if args.bitrate is not None:
        s.audio_bitrate = args.bitrate
    if args.container is not None:
        s.container = args.container
    if args.out_dir is not None:
        s.output_dir = args.out_dir
    if args.suffix is not None:
        s.suffix = args.suffix
    if args.ffmpeg is not None:
        s.ffmpeg_path = args.ffmpeg
    if args.ffprobe is not None:
        s.ffprobe_path = args.ffprobe
    return s


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)
    try:
        ffmpeg, ffprobe = core.resolve_tools(settings)
    except core.GainFrameError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.measure:
        for raw in args.inputs:
            path = Path(raw)
            lufs = core.integrated_lufs(ffmpeg, path, 0, settings)
            print(f"{path}: integrated = {lufs if lufs is None else round(lufs, 2)} LUFS")
        return 0

    failures = 0
    for raw in args.inputs:
        path = Path(raw)
        try:
            result = core.normalize_file(path, settings)
            print(f"OK  {path} -> {result['output']}")
            for note in result["notes"]:
                print(f"    {note}")
        except core.GainFrameError as e:
            failures += 1
            print(f"ERROR {path}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
