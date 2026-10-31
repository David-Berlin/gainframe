"""GainFrame tkinter GUI. One window, stdlib only.

Layout: file list with per-file status, preset dropdown, output options,
a big Normalize button, an overall progress bar, and a log pane.
Processing runs on a worker thread; UI updates are marshalled through a queue.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import core
from .settings import (MODES, PRESET_CUSTOM, PRESETS, Settings, load_settings,
                       save_settings)

VIDEO_TYPES = [
    ("Media files", "*.mp4 *.mkv *.mov *.m4v *.webm *.avi *.ts *.flv *.wmv *.mp3 *.m4a"),
    ("All files", "*.*"),
]


class SettingsWindow(tk.Toplevel):
    def __init__(self, master: "GainFrameApp"):
        super().__init__(master)
        self.app = master
        self.title("GainFrame Settings")
        self.resizable(False, False)
        self.transient(master)
        s = master.settings
        self.vars = {
            "target_i": tk.StringVar(value=str(s.target_i)),
            "target_tp": tk.StringVar(value=str(s.target_tp)),
            "target_lra": tk.StringVar(value=str(s.target_lra)),
            "mode": tk.StringVar(value=s.mode),
            "peak_target_db": tk.StringVar(value=str(s.peak_target_db)),
            "audio_codec": tk.StringVar(value=s.audio_codec),
            "audio_bitrate": tk.StringVar(value=s.audio_bitrate),
            "container": tk.StringVar(value=s.container),
            "ffmpeg_path": tk.StringVar(value=s.ffmpeg_path),
            "ffprobe_path": tk.StringVar(value=s.ffprobe_path),
        }
        rows = [
            ("Target loudness (LUFS)", "target_i", None),
            ("True-peak ceiling (dBTP)", "target_tp", None),
            ("Loudness range LRA (LU)", "target_lra", None),
            ("Mode", "mode", MODES),
            ("Peak target (dB, peak mode)", "peak_target_db", None),
            ("Audio codec ('auto' = by container)", "audio_codec", None),
            ("Audio bitrate", "audio_bitrate", None),
            ("Container ('same' or e.g. mkv)", "container", None),
            ("ffmpeg path override", "ffmpeg_path", None),
            ("ffprobe path override", "ffprobe_path", None),
        ]
        for r, (label, key, choices) in enumerate(rows):
            ttk.Label(self, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            if choices:
                w = ttk.Combobox(self, textvariable=self.vars[key], values=list(choices),
                                 state="readonly", width=30)
            else:
                w = ttk.Entry(self, textvariable=self.vars[key], width=32)
            w.grid(row=r, column=1, padx=8, pady=3)
        btns = ttk.Frame(self)
        btns.grid(row=len(rows), column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _save(self):
        s = self.app.settings
        try:
            s.target_i = float(self.vars["target_i"].get())
            s.target_tp = float(self.vars["target_tp"].get())
            s.target_lra = float(self.vars["target_lra"].get())
            s.peak_target_db = float(self.vars["peak_target_db"].get())
        except ValueError:
            messagebox.showerror("GainFrame", "Loudness, true-peak, LRA and peak values must be numbers.",
                                 parent=self)
            return
        s.mode = self.vars["mode"].get()
        s.audio_codec = self.vars["audio_codec"].get().strip() or "auto"
        s.audio_bitrate = self.vars["audio_bitrate"].get().strip() or "192k"
        s.container = self.vars["container"].get().strip() or "same"
        s.ffmpeg_path = self.vars["ffmpeg_path"].get().strip()
        s.ffprobe_path = self.vars["ffprobe_path"].get().strip()
        save_settings(s)
        self.app.preset_var.set(PRESET_CUSTOM)
        self.app.log("Settings saved.")
        self.destroy()


class GainFrameApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GainFrame - audio normalizer (video untouched)")
        self.geometry("780x620")
        self.settings = load_settings()
        self.files: list[Path] = []
        self.statuses: dict[str, str] = {}
        self.events: queue.Queue = queue.Queue()
        self.working = False
        self._build()
        self.after(100, self._drain)

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="Add Files...", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Remove Selected", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left")
        ttk.Button(top, text="Settings...", command=lambda: SettingsWindow(self)).pack(side="right")

        self.tree = ttk.Treeview(self, columns=("file", "status"), show="headings", height=9)
        self.tree.heading("file", text="File")
        self.tree.heading("status", text="Status")
        self.tree.column("file", width=520)
        self.tree.column("status", width=200)
        self.tree.pack(fill="both", expand=False, **pad)

        opts = ttk.LabelFrame(self, text="Output")
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Preset:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.preset_var = tk.StringVar(value="Streaming -14 LUFS")
        preset = ttk.Combobox(opts, textvariable=self.preset_var, state="readonly", width=22,
                              values=list(PRESETS.keys()) + [PRESET_CUSTOM])
        preset.grid(row=0, column=1, sticky="w", pady=3)
        preset.bind("<<ComboboxSelected>>", self._preset_changed)
        ttk.Label(opts, text="Folder:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.outdir_var = tk.StringVar(value=self.settings.output_dir)
        ttk.Entry(opts, textvariable=self.outdir_var, width=52).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Button(opts, text="Browse...", command=self._browse_outdir).grid(row=1, column=2, padx=4)
        ttk.Label(opts, text="(blank = same folder as input)").grid(row=1, column=3, sticky="w")
        ttk.Label(opts, text="Suffix:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.suffix_var = tk.StringVar(value=self.settings.suffix)
        ttk.Entry(opts, textvariable=self.suffix_var, width=22).grid(row=2, column=1, sticky="w", pady=3)

        run = ttk.Frame(self)
        run.pack(fill="x", **pad)
        self.run_btn = ttk.Button(run, text="Normalize", command=self._start)
        self.run_btn.pack(side="left")
        self.progress = ttk.Progressbar(run, mode="determinate", maximum=1000)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.progress_label = ttk.Label(run, text="")
        self.progress_label.pack(side="left")

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log("Ready. Add video files, pick a preset, hit Normalize.")
        self.log("The video stream is always copied bit-for-bit - only audio is re-encoded.")

    # -------------------------------------------------------------- helpers
    def log(self, text: str):
        def _append():
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            self.events.put(("log", text))

    def _set_status(self, path: Path, status: str):
        self.statuses[str(path)] = status
        for item in self.tree.get_children():
            if self.tree.item(item, "values") and self.tree.item(item, "values")[0] == str(path):
                self.tree.item(item, values=(str(path), status))

    def _add_files(self):
        names = filedialog.askopenfilenames(title="Choose media files", filetypes=VIDEO_TYPES)
        for name in names:
            p = Path(name)
            if p not in self.files:
                self.files.append(p)
                self.statuses[str(p)] = "queued"
                self.tree.insert("", "end", values=(str(p), "queued"))

    def _remove_selected(self):
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if values:
                p = Path(values[0])
                if p in self.files:
                    self.files.remove(p)
            self.tree.delete(item)

    def _clear(self):
        if self.working:
            return
        self.files.clear()
        self.statuses.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _browse_outdir(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.outdir_var.set(d)

    def _preset_changed(self, _event=None):
        if self.preset_var.get() == PRESET_CUSTOM:
            SettingsWindow(self)

    def _effective_settings(self) -> Settings:
        s = Settings.from_dict(self.settings.to_dict())
        preset = self.preset_var.get()
        if preset in PRESETS:
            for k, v in PRESETS[preset].items():
                setattr(s, k, v)
        s.output_dir = self.outdir_var.get().strip()
        s.suffix = self.suffix_var.get().strip() or ".normalized"
        # keep GUI-level output choices for next launch
        self.settings.output_dir = s.output_dir
        self.settings.suffix = s.suffix
        save_settings(self.settings)
        return s

    # ------------------------------------------------------------- pipeline
    def _start(self):
        if self.working:
            return
        if not self.files:
            messagebox.showinfo("GainFrame", "Add at least one file first.")
            return
        settings = self._effective_settings()
        try:
            core.resolve_tools(settings)
        except core.GainFrameError as e:
            messagebox.showerror("GainFrame - ffmpeg not found", str(e))
            return
        self.working = True
        self.run_btn.state(["disabled"])
        files = list(self.files)
        threading.Thread(target=self._worker, args=(files, settings), daemon=True).start()

    def _worker(self, files: list[Path], settings: Settings):
        total = len(files)
        for i, path in enumerate(files):
            def progress(frac, i=i):
                self.events.put(("progress", (i + frac) / total))

            def phase(name, path=path):
                self.events.put(("status", (path, name)))

            def log_cb(line, path=path):
                self.events.put(("log", line))

            self.events.put(("status", (path, "analyzing")))
            try:
                result = core.normalize_file(
                    path, settings, progress_cb=progress, phase_cb=phase, log_cb=log_cb)
                self.events.put(("status", (path, "done")))
                self.events.put(("log", f"DONE {path.name} -> {result['output']}"))
                for note in result["notes"]:
                    self.events.put(("log", f"    {note}"))
            except core.GainFrameError as e:
                self.events.put(("status", (path, "error")))
                self.events.put(("log", f"ERROR {path.name}: {e}"))
            except Exception as e:  # never let the worker die silently
                self.events.put(("status", (path, "error")))
                self.events.put(("log", f"ERROR {path.name}: unexpected {e!r}"))
        self.events.put(("progress", 1.0))
        self.events.put(("finished", None))

    def _drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_text.insert("end", str(payload) + "\n")
                    self.log_text.see("end")
                elif kind == "status":
                    path, status = payload
                    self._set_status(path, status)
                elif kind == "progress":
                    frac = float(payload)
                    self.progress["value"] = int(frac * 1000)
                    self.progress_label.config(text=f"{int(frac * 100)}%")
                elif kind == "finished":
                    self.working = False
                    self.run_btn.state(["!disabled"])
        except queue.Empty:
            pass
        self.after(100, self._drain)


def main():
    app = GainFrameApp()
    app.mainloop()


if __name__ == "__main__":
    main()
