"""AMMB companion window (Tkinter, ships with Python - no extra dependencies).

    python -m ammb.gui        or  ammb gui        or double-click AMMB.bat (Windows)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from . import __version__, audio_io, pipeline, presets

APP_TITLE = f"AMMB by M3HX  v{__version__}"
DEFAULT_PRESET = "psytrance"
REPO_URL = "https://github.com/imehesz/ammb"

LIGHT = {"bg": "#f3f3f3", "fg": "#1a1a1a", "hint": "#666666", "field": "#ffffff",
         "accent": "#0067c0", "accent_fg": "#ffffff", "border": "#c8c8c8", "button": "#fdfdfd"}
DARK = {"bg": "#202124", "fg": "#e8e8e8", "hint": "#9aa0a6", "field": "#2d2e31",
        "accent": "#3b82f6", "accent_fg": "#ffffff", "border": "#44464a", "button": "#35363a"}


def _settings_path() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.path.expanduser("~/.config")
    return Path(base or Path.home()) / "AMMB" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(d: dict) -> None:
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2))
    except OSError:
        pass


class _QueueWriter:
    """File-like object that forwards print() output to the log pane."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


def _open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("620x680")
        self.minsize(520, 560)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.last_out: Path | None = None
        self.preset_names = presets.available()
        self.preset_labels = {n: presets.load(n)["name"] for n in self.preset_names}

        self.settings = load_settings()
        self.dark = tk.BooleanVar(value=bool(self.settings.get("dark_mode", False)))
        self._build_menu()
        self._build()
        self.apply_theme()
        self.after(100, self._pump)
        self.after(300, self.refresh_audacity)

    # ------------------------------------------------------------------ layout
    def _build_menu(self):
        bar = tk.Menu(self)
        settings = tk.Menu(bar, tearoff=False)
        settings.add_checkbutton(label="Dark mode", variable=self.dark, command=self._toggle_dark)
        bar.add_cascade(label="Settings", menu=settings)
        bar.add_command(label="About", command=self.show_about)
        self.config(menu=bar)

    ABOUT_TEXT = (
        "AMMB masters a song from its stems. It sums your tracks at the levels you set, "
        "leaves clean headroom, and then masters the result with Matchering: an EQ that "
        "matches a target's tonal balance, plus a loudness limiter. The target is either a "
        "genre preset or a reference song you love.",

        "Audacity tab: open your project in Audacity 3 (with mod-script-pipe enabled under "
        "Preferences > Modules). AMMB lists your tracks: every audio track is a stem, a track "
        "named REF is the reference, and a track whose name starts with # is left out. Pick a "
        "preset (or \"Reference track\"), press MASTER IT, and a FINAL_MASTER track appears in "
        "your session with the stems muted, ready to A/B.",

        "Stems folder tab: works with stems from any DAW. Choose a folder of audio files (all "
        "starting at the same time), a target, and where to save. You get final_master.wav plus "
        "a report.",

        "Compare spectrum shows your stem mix against the target before you master, so you can "
        "see where it's heavier or lighter (red = more, green = less). Big gaps are usually best "
        "fixed in the mix itself. Auto-balance is experimental: it moves stem levels toward the "
        "target, but your own levels usually sound better.",
    )

    def show_about(self):
        c = DARK if self.dark.get() else LIGHT
        win = tk.Toplevel(self)
        win.title("About AMMB")
        win.configure(background=c["bg"])
        win.resizable(False, False)
        win.transient(self)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=APP_TITLE, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(body, text="Audacity Auto-Mix & Master Bridge", style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        for para in self.ABOUT_TEXT:
            ttk.Label(body, text=para, wraplength=460, justify="left").pack(anchor="w", pady=(0, 8))
        foot = ttk.Frame(body)
        foot.pack(fill="x", pady=(6, 0))
        link = ttk.Label(foot, text=REPO_URL, foreground=c["accent"], cursor="hand2",
                         font=("Segoe UI", 9, "underline"))
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: __import__("webbrowser").open(REPO_URL))
        ttk.Button(foot, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        self._dark_title_bar_for(win)

    def _toggle_dark(self):
        self.settings["dark_mode"] = self.dark.get()
        save_settings(self.settings)
        self.apply_theme()

    def apply_theme(self):
        c = DARK if self.dark.get() else LIGHT
        st = ttk.Style(self)
        # 'clam' is the built-in theme that honours custom colours on every platform
        st.theme_use("clam")
        st.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"],
                     bordercolor=c["border"], lightcolor=c["bg"], darkcolor=c["bg"],
                     troughcolor=c["field"], selectbackground=c["accent"], selectforeground=c["accent_fg"],
                     insertcolor=c["fg"], font=("Segoe UI", 9))
        st.configure("TButton", background=c["button"], padding=(10, 4))
        st.map("TButton", background=[("disabled", c["bg"]), ("active", c["field"])],
               foreground=[("disabled", c["hint"])])
        st.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=8,
                     background="#2e9e44", foreground="#ffffff", bordercolor="#23803a",
                     lightcolor="#3cb554", darkcolor="#23803a")
        st.map("Big.TButton",
               background=[("disabled", "#5f7f66" if self.dark.get() else "#a9cdb2"),
                           ("pressed", "#23803a"), ("active", "#38b350")],
               foreground=[("disabled", "#d9e6dc")])
        st.configure("Hint.TLabel", foreground=c["hint"])
        st.configure("TLabelframe", background=c["bg"], bordercolor=c["border"])
        st.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
        st.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
        st.configure("TNotebook.Tab", background=c["button"], foreground=c["fg"], padding=(10, 4))
        st.map("TNotebook.Tab", background=[("selected", c["bg"])], expand=[("selected", (1, 1, 1, 0))])
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=c["bg"], foreground=c["fg"], indicatorbackground=c["field"])
            st.map(w, background=[("active", c["bg"])], foreground=[("disabled", c["hint"])],
                   indicatorbackground=[("selected", c["accent"]), ("disabled", c["bg"])])
        st.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"])
        st.map("TEntry", fieldbackground=[("disabled", c["bg"])], foreground=[("disabled", c["hint"])])
        st.configure("TCombobox", fieldbackground=c["field"], foreground=c["fg"], background=c["button"],
                     arrowcolor=c["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", c["field"]), ("disabled", c["bg"])],
               foreground=[("disabled", c["hint"])], selectbackground=[("readonly", c["field"])],
               selectforeground=[("readonly", c["fg"])])
        st.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["fg"],
                     rowheight=20)
        st.configure("Treeview.Heading", background=c["button"], foreground=c["fg"])
        st.map("Treeview", background=[("selected", c["accent"])], foreground=[("selected", c["accent_fg"])])
        st.configure("TScrollbar", background=c["button"], arrowcolor=c["fg"])
        st.configure("Horizontal.TProgressbar", background=c["accent"])
        # classic-Tk pieces: combobox dropdown list + window background
        self.option_add("*TCombobox*Listbox.background", c["field"])
        self.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.configure(background=c["bg"])
        self._dark_title_bar(self.dark.get())

    def _dark_title_bar(self, on: bool):
        """Windows 10/11: ask DWM for a dark title bar. Silently ignored elsewhere."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if on else 0)
            for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
            # nudge Windows to repaint the frame
            self.withdraw()
            self.deiconify()
        except Exception:
            pass

    def _build(self):
        pad = {"padx": 10, "pady": 4}

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="x", padx=10, pady=(10, 4))
        self.tab_au = ttk.Frame(self.tabs)
        self.tab_off = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_au, text="  Audacity  ")
        self.tabs.add(self.tab_off, text="  Stems folder  ")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._sync_target_state())

        # --- Audacity tab
        top = ttk.Frame(self.tab_au)
        top.pack(fill="x", **pad)
        self.au_status = ttk.Label(top, text="Looking for Audacity ...")
        self.au_status.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_audacity).pack(side="right")
        cols = ("name", "role", "len")
        self.track_list = ttk.Treeview(self.tab_au, columns=cols, show="headings", height=7)
        for c, w, t in (("name", 260, "Track"), ("role", 170, "Role"), ("len", 90, "Length")):
            self.track_list.heading(c, text=t)
            self.track_list.column(c, width=w, anchor="w")
        self.track_list.pack(fill="x", **pad)
        ttk.Label(self.tab_au, style="Hint.TLabel",
                  text="Needs Audacity 3 with mod-script-pipe enabled. Name the reference track REF;\n"
                       "start a track name with # to leave it out.").pack(anchor="w", **pad)

        # --- Stems folder tab
        self.stems_dir = tk.StringVar()
        self.ref_file = tk.StringVar()
        self.out_dir = tk.StringVar()
        for label, var, cmd in (("Stems folder", self.stems_dir, self._pick_stems),
                                ("Reference file", self.ref_file, self._pick_ref),
                                ("Output folder", self.out_dir, self._pick_out)):
            row = ttk.Frame(self.tab_off)
            row.pack(fill="x", **pad)
            ttk.Label(row, text=label, width=14).pack(side="left")
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
            btn = ttk.Button(row, text="Browse...", command=cmd)
            btn.pack(side="right")
            if var is self.ref_file:
                self.ref_row = (ent, btn)
        self.stems_info = ttk.Label(self.tab_off, style="Hint.TLabel", text="Every audio file in the folder is a stem (REF.* is skipped).")
        self.stems_info.pack(anchor="w", **pad)

        # --- shared target + options
        tgt = ttk.LabelFrame(self, text="Master towards")
        tgt.pack(fill="x", padx=10, pady=4)
        self.target = tk.StringVar(value="preset")
        r1 = ttk.Frame(tgt)
        r1.pack(fill="x", **pad)
        ttk.Radiobutton(r1, text="Genre preset", value="preset", variable=self.target,
                        command=self._sync_target_state).pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(r1, state="readonly", width=34,
                                       values=[self.preset_labels[n] for n in self.preset_names],
                                       textvariable=self.preset_var)
        if DEFAULT_PRESET in self.preset_names:
            self.preset_box.current(self.preset_names.index(DEFAULT_PRESET))
        self.preset_box.pack(side="left", padx=10)
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: self._show_preset_desc())
        self.preset_desc = ttk.Label(tgt, style="Hint.TLabel", wraplength=560, justify="left")
        self.preset_desc.pack(anchor="w", fill="x", padx=(34, 10))
        # wrap the description to whatever width the window has
        tgt.bind("<Configure>", lambda e: self.preset_desc.configure(wraplength=max(200, e.width - 50)))
        self.ref_radio = ttk.Radiobutton(tgt, text="Reference track (REF)", value="ref", variable=self.target,
                                         command=self._sync_target_state)
        self.ref_radio.pack(anchor="w", **pad)

        opt = ttk.LabelFrame(self, text="Options")
        opt.pack(fill="x", padx=10, pady=4)
        self.balance = tk.BooleanVar(value=False)
        self.compare = tk.BooleanVar(value=False)
        self.keep = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Auto-balance stem levels (experimental - otherwise your levels are kept)",
                        variable=self.balance).pack(anchor="w", **pad)
        ttk.Checkbutton(opt, text="Also make a version with my original levels, for A/B (only with auto-balance)",
                        variable=self.compare).pack(anchor="w", **pad)
        self.keep_cb = ttk.Checkbutton(opt, text="Audacity tab: keep the exported stems + report (pick a folder)",
                                       variable=self.keep, command=self._pick_keep)
        self.keep_cb.pack(anchor="w", **pad)
        self.keep_dir: Path | None = None

        # --- action + log
        act = ttk.Frame(self)
        act.pack(fill="x", padx=10, pady=6)
        self.go = ttk.Button(act, text="MASTER IT", style="Big.TButton", command=self.start)
        self.go.pack(side="left")
        self.open_btn = ttk.Button(act, text="Open output folder", command=self._open_out, state="disabled")
        self.open_btn.pack(side="left", padx=10)
        self.cmp_btn = ttk.Button(act, text="Compare spectrum", command=self.compare_spectrum)
        self.cmp_btn.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="indeterminate", length=160)
        # only shown while a job runs (see start / _pump)

        logf = ttk.Frame(self)
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log = tk.Text(logf, height=12, wrap="word", font=("Consolas", 9), state="disabled",
                           background="#111", foreground="#9f9", insertbackground="#9f9")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        self._show_preset_desc()
        self._sync_target_state()

    # ------------------------------------------------------------------ helpers
    def _mode(self) -> str:
        return "audacity" if self.tabs.index(self.tabs.select()) == 0 else "offline"

    def _preset_id(self) -> str:
        return self.preset_names[self.preset_box.current()]

    def _show_preset_desc(self):
        if self.preset_box.current() >= 0:
            self.preset_desc.configure(text=presets.load(self._preset_id()).get("description", ""))

    def _sync_target_state(self):
        is_ref = self.target.get() == "ref"
        self.preset_box.configure(state="disabled" if is_ref else "readonly")
        ent, btn = self.ref_row
        state = "normal" if is_ref else "disabled"
        ent.configure(state=state)
        btn.configure(state=state)
        self.ref_radio.configure(text="Reference track (the track named REF)" if self._mode() == "audacity"
                                 else "Reference file (below)")

    def _write(self, s: str):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pick_stems(self):
        d = filedialog.askdirectory(title="Folder with your stems")
        if d:
            self.stems_dir.set(d)
            if not self.out_dir.get():
                self.out_dir.set(str(Path(d) / "ammb_out"))
            files = [p.name for p in audio_io.list_audio(d) if p.stem.lower() != "ref"]
            self.stems_info.configure(text=f"{len(files)} stems: " + ", ".join(files[:8]) + (" ..." if len(files) > 8 else ""))
            ref = next((p for p in audio_io.list_audio(d) if p.stem.lower() == "ref"), None)
            if ref and not self.ref_file.get():
                self.ref_file.set(str(ref))

    def _pick_ref(self):
        f = filedialog.askopenfilename(title="Reference song",
                                       filetypes=[("Audio", "*.wav *.flac *.aif *.aiff *.ogg"), ("All", "*.*")])
        if f:
            self.ref_file.set(f)

    def _pick_out(self):
        d = filedialog.askdirectory(title="Where to save the master")
        if d:
            self.out_dir.set(d)

    def _pick_keep(self):
        if self.keep.get():
            d = filedialog.askdirectory(title="Keep Audacity working files in ...")
            if d:
                self.keep_dir = Path(d)
            else:
                self.keep.set(False)

    def _open_out(self):
        if self.last_out:
            _open_folder(self.last_out)

    # ------------------------------------------------------------------ Audacity status
    def refresh_audacity(self):
        if self.busy:
            return
        self.au_status.configure(text="Looking for Audacity ...")

        def work():
            from . import bridge
            try:
                self.q.put(("session", bridge.inspect()))
            except Exception as e:  # PipeError or anything odd
                self.q.put(("session", e))

        threading.Thread(target=work, daemon=True).start()

    def _show_session(self, info):
        self.track_list.delete(*self.track_list.get_children())
        if isinstance(info, Exception):
            self.au_status.configure(text="●  Audacity not connected", foreground="#c33")
            self.track_list.insert("", "end", values=("Open Audacity 3 with mod-script-pipe enabled,", "then press Refresh", ""))
            return
        n, ref = info["stems"], " + REF" if info["has_ref"] else ""
        self.au_status.configure(text=f"●  Audacity connected  -  {n} stems{ref}, {info['end']:.1f} s",
                                 foreground="#2a2")
        for t, role in zip(info["tracks"], info["roles"]):
            ln = f"{t.get('end', 0) - t.get('start', 0):.1f} s" if t.get("kind") == "wave" else ""
            self.track_list.insert("", "end", values=(t.get("name", ""), role, ln))
        self.session = info

    # ------------------------------------------------------------------ run
    def start(self):
        if self.busy:
            return
        mode = self._mode()
        use_preset = self.target.get() == "preset"
        opts = pipeline.Options(balance=self.balance.get(), compare=self.compare.get() and self.balance.get())
        preset_id = self._preset_id() if use_preset else None   # read Tk widgets here, never in the worker

        if mode == "offline":
            stems_dir = Path(self.stems_dir.get())
            if not stems_dir.is_dir():
                messagebox.showwarning(APP_TITLE, "Pick the folder with your stems first.")
                return
            ref = None if use_preset else Path(self.ref_file.get())
            if ref is not None and not ref.is_file():
                messagebox.showwarning(APP_TITLE, "Pick a reference file, or choose a genre preset.")
                return
            out = Path(self.out_dir.get() or stems_dir / "ammb_out")
            stems = [p.resolve() for p in audio_io.list_audio(stems_dir)
                     if p.stem.lower() != "ref" and not p.name.startswith("preset_reference_")
                     and (ref is None or p.resolve() != ref.resolve())]
            if not stems:
                messagebox.showwarning(APP_TITLE, "No audio files found in that folder.")
                return
            job = lambda: self._run_offline(stems, ref, out, opts, preset_id)
        else:
            info = getattr(self, "session", None)
            if not info or not info["stems"]:
                messagebox.showwarning(APP_TITLE, "No Audacity session with stems found. Press Refresh.")
                return
            if not use_preset and not info["has_ref"]:
                messagebox.showwarning(APP_TITLE, "There's no track named REF. Rename your reference track to REF, "
                                                  "or choose a genre preset.")
                return
            keep = self.keep_dir if self.keep.get() else None
            job = lambda: self._run_audacity(opts, keep, preset_id)

        self.busy = True
        self.go.configure(state="disabled")
        self.cmp_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.progress.pack(side="right")
        self.progress.start(12)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        threading.Thread(target=self._worker, args=(job,), daemon=True).start()

    def _worker(self, job):
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _QueueWriter(self.q)
        try:
            ok, out = job()
        except Exception as e:  # never kill the window
            import traceback
            print(f"\nERROR: {e}\n{traceback.format_exc()}")
            ok, out = False, None
        finally:
            sys.stdout, sys.stderr = old
        self.q.put(("done", (ok, out)))

    def _run_offline(self, stems, ref, out, opts, preset_id):
        out.mkdir(parents=True, exist_ok=True)
        if preset_id:
            import soundfile as sf
            ref = presets.prepare(preset_id, out, sf.info(str(stems[0])).samplerate, opts)
        rep = pipeline.run(stems, ref, out, opts)
        print(pipeline.format_report(rep))
        print(f"✓ Done in {rep['total_seconds']} s  ->  {out / 'final_master.wav'}")
        return True, out

    def _run_audacity(self, opts, keep, preset_id):
        from . import bridge
        rc = bridge.run(opts, keep, preset=preset_id)
        if rc == 0:
            print("✓ FINAL_MASTER is in your Audacity session.")
        return rc == 0, keep

    def compare_spectrum(self):
        if self.busy:
            return
        mode = self._mode()
        use_preset = self.target.get() == "preset"
        preset_id = self._preset_id() if use_preset else None
        stems_dir = Path(self.stems_dir.get()) if self.stems_dir.get() else None
        ref_file = None if use_preset else (self.ref_file.get() or None)
        if mode == "offline":
            if not stems_dir or not stems_dir.is_dir():
                messagebox.showwarning(APP_TITLE, "Pick the folder with your stems first.")
                return
            if not use_preset and not (ref_file and Path(ref_file).is_file()):
                messagebox.showwarning(APP_TITLE, "Pick a reference file, or choose a genre preset.")
                return
        else:
            info = getattr(self, "session", None)
            if not info or not info["stems"]:
                messagebox.showwarning(APP_TITLE, "No Audacity session with stems found. Press Refresh.")
                return
            if not use_preset and not info["has_ref"]:
                messagebox.showwarning(APP_TITLE, "There's no track named REF - choose a genre preset instead.")
                return
        self.busy = True
        self.go.configure(state="disabled")
        self.cmp_btn.configure(state="disabled")
        self.progress.pack(side="right")
        self.progress.start(12)

        def work():
            old = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = _QueueWriter(self.q)
            try:
                self.q.put(("spectrum", _compare_job(self, mode, use_preset, preset_id, stems_dir, ref_file)))
            except Exception as e:
                self.q.put(("spectrum", e))
            finally:
                sys.stdout, sys.stderr = old

        threading.Thread(target=work, daemon=True).start()

    def _show_spectrum(self, mix_c, tgt_c, target_label, source_label):
        from . import spectrum
        c = DARK if self.dark.get() else LIGHT
        mix_col, tgt_col = ("#4fc3f7", "#ffb74d") if self.dark.get() else ("#0067c0", "#e65100")
        grid_col = "#3a3b3f" if self.dark.get() else "#dddddd"

        win = tk.Toplevel(self)
        win.title(f"Spectrum - {source_label} vs {target_label}")
        win.configure(background=c["bg"])
        W, H = 760, 380
        L, R, T, B = 52, 16, 16, 34
        cv = tk.Canvas(win, width=W, height=H, background=c["field"], highlightthickness=0)
        cv.pack(padx=10, pady=(10, 4))

        lo_f, hi_f = np.log10(20), np.log10(20000)
        vals = np.concatenate([mix_c, tgt_c])
        top = 6 * np.ceil((vals.max() + 2) / 6)
        # show at most 48 dB of range; anything quieter (e.g. nothing at 20 Hz) sits on the floor
        bot = max(6 * np.floor((vals.min() - 2) / 6), top - 48)
        raw_mix, raw_tgt = mix_c, tgt_c
        mix_c, tgt_c = np.maximum(mix_c, bot), np.maximum(tgt_c, bot)
        X = lambda f: L + (np.log10(f) - lo_f) / (hi_f - lo_f) * (W - L - R)
        Y = lambda d: T + (top - d) / (top - bot) * (H - T - B)

        for f, lab in ((31.5, "31"), (63, "63"), (125, "125"), (250, "250"), (500, "500"), (1000, "1k"),
                       (2000, "2k"), (4000, "4k"), (8000, "8k"), (16000, "16k")):
            cv.create_line(X(f), T, X(f), H - B, fill=grid_col)
            cv.create_text(X(f), H - B + 14, text=lab, fill=c["hint"], font=("Segoe UI", 8))
        d = bot
        while d <= top:
            cv.create_line(L, Y(d), W - R, Y(d), fill=grid_col)
            cv.create_text(L - 8, Y(d), text=f"{d:+.0f}", anchor="e", fill=c["hint"], font=("Segoe UI", 8))
            d += 6
        cv.create_text(14, T + (H - T - B) / 2, text="dB", angle=90, fill=c["hint"], font=("Segoe UI", 8))

        def blend(hex_a, hex_b, t):
            a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(a, b))

        more_col, less_col = blend("#e53935", c["field"], 0.35), blend("#43a047", c["field"], 0.35)
        # shade where the mix differs from the target
        for k in range(len(spectrum.CENTERS) - 1):
            f0, f1 = spectrum.CENTERS[k], spectrum.CENTERS[k + 1]
            m0, m1, t0, t1 = mix_c[k], mix_c[k + 1], tgt_c[k], tgt_c[k + 1]
            col = more_col if (m0 + m1) > (t0 + t1) else less_col
            cv.create_polygon(X(f0), Y(m0), X(f1), Y(m1), X(f1), Y(t1), X(f0), Y(t0),
                              fill=col, outline="")

        def line(cur, col, dash=None):
            pts = []
            for f, v in zip(spectrum.CENTERS, cur):
                pts += [X(f), Y(v)]
            cv.create_line(*pts, fill=col, width=3, smooth=True, dash=dash)

        line(tgt_c, tgt_col, dash=(6, 3))
        line(mix_c, mix_col)

        leg = ttk.Frame(win)
        leg.pack(fill="x", padx=12)
        for col, text, dash in ((mix_col, source_label, None), (tgt_col, f"target: {target_label}", (6, 3))):
            sw = tk.Canvas(leg, width=28, height=12, background=c["bg"], highlightthickness=0)
            sw.create_line(2, 6, 26, 6, fill=col, width=3, dash=dash)
            sw.pack(side="left")
            ttk.Label(leg, text=text).pack(side="left", padx=(2, 16))
        ttk.Label(leg, text="red = more than target, green = less", style="Hint.TLabel").pack(side="right")

        box = ttk.LabelFrame(win, text="Biggest differences (shape only - loudness is aligned)")
        box.pack(fill="x", padx=10, pady=10)
        for row in spectrum.describe(raw_mix, raw_tgt):
            ttk.Label(box, text="•  " + row).pack(anchor="w", padx=10, pady=1)
        ttk.Label(box, style="Hint.TLabel",
                  text="Mastering (Matchering EQ) will pull the mix toward the target; big gaps are often\n"
                       "better fixed in the mix itself (a stem's level or EQ).").pack(anchor="w", padx=10, pady=(4, 6))
        self._dark_title_bar_for(win)

    def _dark_title_bar_for(self, win):
        if sys.platform != "win32" or not self.dark.get():
            return
        try:
            import ctypes
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            val = ctypes.c_int(1)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
        except Exception:
            pass

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "spectrum":
                    self.busy = False
                    self.progress.stop()
                    self.progress.pack_forget()
                    self.go.configure(state="normal")
                    self.cmp_btn.configure(state="normal")
                    if isinstance(payload, Exception):
                        self._write(f"\n✗ Spectrum failed: {payload}\n")
                    else:
                        self._show_spectrum(*payload)
                elif kind == "session":
                    self._show_session(payload)
                elif kind == "done":
                    ok, out = payload
                    self.busy = False
                    self.progress.stop()
                    self.progress.pack_forget()
                    self.go.configure(state="normal")
                    self.cmp_btn.configure(state="normal")
                    self.last_out = out
                    if out:
                        self.open_btn.configure(state="normal")
                    if not ok:
                        self._write("\n✗ Something went wrong - see above.\n")
                    if self._mode() == "audacity":
                        self.refresh_audacity()
        except queue.Empty:
            pass
        self.after(100, self._pump)


# ---------------------------------------------------------------------- spectrum view

def _compare_job(app: "App", mode, use_preset, preset_id, stems_dir, ref_file):
    """Runs in a worker thread. -> (mix_curve, target_curve, target_label, source_label)"""
    import tempfile
    from . import spectrum
    tmp = Path(tempfile.mkdtemp(prefix="ammb_spec_"))
    try:
        if mode == "audacity":
            from . import bridge
            print("Exporting your stem mix from Audacity ...")
            mix_path, ref_path = bridge.export_mix(tmp, with_ref=not use_preset)
            mix, sr = audio_io.load(mix_path)
            source = "your stem mix (Audacity)"
        else:
            files = [p for p in audio_io.list_audio(stems_dir)
                     if p.stem.lower() != "ref" and not p.name.startswith("preset_reference_")
                     and (ref_file is None or p.resolve() != Path(ref_file).resolve())]
            if not files:
                raise RuntimeError("no audio files in the stems folder")
            print(f"Summing {len(files)} stems ...")
            sr, mix = None, None
            for f in files:
                x, sr_i = audio_io.load(f, sr)
                sr = sr or sr_i
                if mix is None:
                    mix = x
                else:
                    n = max(len(mix), len(x))
                    mix = audio_io.pad_to(mix, n) + audio_io.pad_to(x, n)
            ref_path = ref_file
            source = f"your stem mix ({len(files)} stems)"
        print("Measuring spectrum ...")
        mix_c = spectrum.curve_of(mix, sr)
        if use_preset:
            p = presets.load(preset_id)
            tgt_c, label = spectrum.curve_of_preset(p), p["name"]
        else:
            r, rsr = audio_io.load(ref_path)
            tgt_c, label = spectrum.curve_of(r, rsr), f"REF ({Path(ref_path).name})"
        mix_c, tgt_c = spectrum.align(mix_c, tgt_c)
        print("✓ Spectrum ready.")
        return mix_c, tgt_c, label, source
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
