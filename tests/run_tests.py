#!/usr/bin/env python3
"""GainFrame end-to-end tests (requires ffmpeg + ffprobe on PATH).

Generates a QUIET multi-track video and a LOUD video with lavfi, runs the
real pipeline (default two-pass loudnorm + a peak-mode run), and asserts:
  1. output integrated loudness within +/-1.0 LU of -14 (both files, both tracks)
  2. video stream bit-identical (stream-copy MD5) input vs output
  3. audio is the only stream whose hash changed; subtitle hash unchanged
  4. duration preserved within tolerance; output probes cleanly
  5. peak mode lands max peak at target +/-0.3 dB
  6. smoke: settings round-trip + command builder honors -c:v copy

Run:  python3 tests/run_tests.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gainframe import core
from gainframe.settings import Settings, load_settings, save_settings

WORK = Path(tempfile.mkdtemp(prefix="gainframe-tests-"))
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if not ok:
        FAILURES.append(name)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("command failed:", " ".join(cmd))
        print(r.stderr[-2000:])
        raise SystemExit(2)
    return r


def stream_md5(ffmpeg: str, path: Path, spec: str) -> str:
    r = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", spec,
                        "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"md5 failed for {path} {spec}: {r.stderr}")
    return r.stdout.strip().split("=")[-1]


def make_inputs(ffmpeg: str) -> tuple[Path, Path]:
    srt = WORK / "subs.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nHello GainFrame\n\n"
                   "2\n00:00:03,000 --> 00:00:05,500\nVideo untouched, promise\n",
                   encoding="utf-8")
    quiet = WORK / "quiet.mp4"
    run([ffmpeg, "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=6",
         "-i", str(srt),
         "-filter_complex", "[1:a]volume=0.04[a1];[2:a]volume=0.25[a2]",
         "-map", "0:v", "-map", "[a1]", "-map", "[a2]", "-map", "3:s",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-c:s", "mov_text", str(quiet)])
    loud = WORK / "loud.mp4"
    # NB: ffmpeg's sine source peaks near -18 dBFS natively, so amplify x4.5
    # (approx +13 dB, no clipping) to get a genuinely loud file peaking ~-5 dBFS.
    run([ffmpeg, "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=6",
         "-filter_complex", "[1:a]volume=4.5[a1]",
         "-map", "0:v", "-map", "[a1]",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", str(loud)])
    return quiet, loud


def main() -> int:
    settings = Settings()  # defaults: two-pass loudnorm, -14 LUFS
    ffmpeg, ffprobe = core.resolve_tools(settings)
    print(f"ffmpeg: {ffmpeg}\nworkdir: {WORK}\n")

    # ---- smoke tests ------------------------------------------------------
    tmp_settings = WORK / "settings.json"
    s2 = Settings(target_i=-16.0, mode="dynamic", suffix=".gain")
    save_settings(s2, tmp_settings)
    s3 = load_settings(tmp_settings)
    check("smoke: settings round-trip",
          s3.target_i == -16.0 and s3.mode == "dynamic" and s3.suffix == ".gain")
    probe_cmd = core.build_command(ffmpeg, WORK / "x.mp4", WORK / "x.normalized.mp4",
                                   Settings(), ["loudnorm=I=-14"])
    joined = " ".join(probe_cmd)
    check("smoke: command copies video", "-c:v copy" in joined and "-map 0" in joined)

    # ---- generate + measure ----------------------------------------------
    quiet, loud = make_inputs(ffmpeg)
    for name, path, tracks in (("quiet", quiet, (0, 1)), ("loud", loud, (0,))):
        for t in tracks:
            lufs = core.integrated_lufs(ffmpeg, path, t, settings)
            peak = core.measure_peak_db(ffmpeg, path, t)
            print(f"input {name} audio{t}: integrated = {lufs:.2f} LUFS, "
                  f"max peak = {peak:.2f} dBFS")

    # ---- default two-pass run ---------------------------------------------
    results = {}
    for path in (quiet, loud):
        results[path.name] = core.normalize_file(path, settings)
        print(f"normalized {path.name}: {results[path.name]['notes']}")

    for name, path, tracks in (("quiet", quiet, (0, 1)), ("loud", loud, (0,))):
        out = Path(results[name + ".mp4"]["output"])
        info_in, info_out = core.probe(ffprobe, path), core.probe(ffprobe, out)
        for t in tracks:
            lufs = core.integrated_lufs(ffmpeg, out, t, settings)
            check(f"{name} audio{t} loudness -> -14 +/-1.0",
                  lufs is not None and abs(lufs - (-14.0)) <= 1.0,
                  f"measured {lufs:.2f} LUFS")
        v_in, v_out = stream_md5(ffmpeg, path, "0:v"), stream_md5(ffmpeg, out, "0:v")
        check(f"{name} video stream bit-identical", v_in == v_out, f"md5 {v_in[:12]}...")
        for t in tracks:
            a_in = stream_md5(ffmpeg, path, f"0:a:{t}")
            a_out = stream_md5(ffmpeg, out, f"0:a:{t}")
            check(f"{name} audio{t} stream changed", a_in != a_out)
        d_in, d_out = core.duration_of(info_in), core.duration_of(info_out)
        check(f"{name} duration preserved", abs(d_in - d_out) <= 0.35,
              f"{d_in:.2f}s -> {d_out:.2f}s")

    s_in = stream_md5(ffmpeg, quiet, "0:s:0")
    s_out = stream_md5(ffmpeg, Path(results["quiet.mp4"]["output"]), "0:s:0")
    check("quiet subtitle stream bit-identical", s_in == s_out)

    # ---- peak mode ---------------------------------------------------------
    peak_settings = Settings(mode="peak", peak_target_db=-1.0)
    peak_result = core.normalize_file(loud, peak_settings,
                                      output_path=WORK / "loud_peak.mp4")
    peak = core.measure_peak_db(ffmpeg, Path(peak_result["output"]), 0)
    check("peak mode lands at -1.0 +/-0.3 dB",
          peak is not None and abs(peak - (-1.0)) <= 0.3,
          f"measured max peak {peak:.2f} dB")

    print()
    if FAILURES:
        print("FAILURES:", ", ".join(FAILURES))
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
