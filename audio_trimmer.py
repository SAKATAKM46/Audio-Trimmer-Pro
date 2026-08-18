import os
import sys
import math
import random
import tempfile
import winsound
import threading
import subprocess
import json
import customtkinter as ctk
from tkinter import filedialog, messagebox, Canvas

# --- 1. Εξαφάνιση Μαύρου Παραθύρου Τερματικού (Windows Subprocess Fix) ---
if sys.platform == "win32":
    _orig_popen = subprocess.Popen
    def _silent_popen(*args, **kwargs):
        creationflags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = creationflags | subprocess.CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)
    subprocess.Popen = _silent_popen

# --- Δήλωση FFmpeg από τον φάκελο bin ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(SCRIPT_DIR, "bin")
os.environ["PATH"] += os.pathsep + BIN_DIR

from pydub import AudioSegment
from pydub.silence import detect_leading_silence
from pydub.utils import mediainfo

AudioSegment.converter = os.path.join(BIN_DIR, "ffmpeg.exe")
AudioSegment.ffprobe = os.path.join(BIN_DIR, "ffprobe.exe")
# ----------------------------------------

SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "single_after_save": "reset",       # "reset" | "keep"
    "batch_after_save": "reset",        # "reset" | "keep"
    "batch_output_dir": "",             # "" -> ρωτάει φάκελο στην έναρξη μαζικής επεξεργασίας
    "batch_confirm_overwrite": True     # True -> ρωτάει πριν αντικαταστήσει υπάρχον αρχείο
}

AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class AudioTrimmerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Audio Trimmer Pro v2.6")
        self.geometry("580x760")
        self.resizable(False, False)

        self.file_path = None
        self.audio = None
        self.duration_sec = 0
        self.tags = {}

        self.settings = load_settings()

        # --- Batch Queue State ---
        self.queue_files = []       
        self.queue_index = -1       
        self.batch_output_dir = None  

        self.PREVIEW_MS = 5500  
        self._play_reset_jobs = {}  

        # --- Keyboard Shortcuts ---
        self.bind("<Control-o>", lambda e: self.load_file())
        self.bind("<Return>", lambda e: self.process_and_save())

        # --- File Selection ---
        self.file_frame = ctk.CTkFrame(self)
        self.file_frame.pack(fill="x", padx=15, pady=10)

        self.btn_open = ctk.CTkButton(self.file_frame, text="Άνοιγμα (Ctrl+O)", command=self.load_file)
        self.btn_open.pack(side="left", padx=(10, 5), pady=10)

        self.btn_open_folder = ctk.CTkButton(self.file_frame, text="Άνοιγμα Φακέλου", width=110, fg_color="#8F4427", hover_color="#612617", command=self.load_folder)
        self.btn_open_folder.pack(side="left", padx=5, pady=10)

        # Το γρανάζι πακετάρεται ΠΡΙΝ το lbl_file, ώστε να κλειδώνει πάντα τη θέση του
        # στα δεξιά (fill="x"+expand=True στο label δεν θα "τρώει" τον χώρο του)
        self.btn_settings = ctk.CTkButton(self.file_frame, text="⚙", width=32, fg_color="#6A2C2C", hover_color="#760e0e", command=self.open_settings_window)
        self.btn_settings.pack(side="right", padx=(5, 10), pady=10)

        self.lbl_file = ctk.CTkLabel(self.file_frame, text="Δεν έχει επιλεγεί αρχείο", anchor="w")
        self.lbl_file.pack(side="left", fill="x", expand=True, padx=10)

        # --- Queue Progress ---
        self.lbl_queue = ctk.CTkLabel(self, text="", text_color="#5cb85c", font=("Arial", 12, "bold"))
        self.lbl_queue.pack(pady=(0, 2))

        self.lbl_info = ctk.CTkLabel(self, text="Διάρκεια: - | Format: -", text_color="gray")
        self.lbl_info.pack(pady=2)

        # --- Trimming & Auto Silence Section ---
        self.trim_frame = ctk.CTkFrame(self)
        self.trim_frame.pack(fill="x", padx=15, pady=10)

        header_trim = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        header_trim.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(header_trim, text="Περικοπή & Αυτόματα Κενά", font=("Arial", 14, "bold")).pack(side="left")
        
        self.btn_auto_silence = ctk.CTkButton(header_trim, text="Auto Trim Κενών", width=110, fg_color="#1f538d", command=self.auto_detect_silence)
        self.btn_auto_silence.pack(side="right")

        grid_trim = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        grid_trim.pack(pady=5)

        # Start Trim
        ctk.CTkLabel(grid_trim, text="Από Αρχή (sec):").grid(row=0, column=0, padx=5, pady=5)
        self.ent_start = ctk.CTkEntry(grid_trim, width=70, placeholder_text="0.0")
        self.ent_start.grid(row=0, column=1, padx=5, pady=5)
        self.btn_play_start = ctk.CTkButton(grid_trim, text="▶", width=35, command=self.preview_start)
        self.btn_play_start.grid(row=0, column=2, padx=5, pady=5)
        self.eq_start = self._create_equalizer(grid_trim)
        self.eq_start.grid(row=0, column=3, padx=(10, 5), pady=5)

        # End Trim
        ctk.CTkLabel(grid_trim, text="Από Τέλος (sec):").grid(row=1, column=0, padx=5, pady=5)
        self.ent_end = ctk.CTkEntry(grid_trim, width=70, placeholder_text="0.0")
        self.ent_end.grid(row=1, column=1, padx=5, pady=5)
        self.btn_play_end = ctk.CTkButton(grid_trim, text="▶", width=35, command=self.preview_end)
        self.btn_play_end.grid(row=1, column=2, padx=5, pady=5)
        self.eq_end = self._create_equalizer(grid_trim)
        self.eq_end.grid(row=1, column=3, padx=(10, 5), pady=5)

        self.EQ_COLORS = {
            self.btn_play_start: {"bright": "#2ecc71", "dim": "#3d3d3d"},
            self.btn_play_end: {"bright": "#2ecc71", "dim": "#3d3d3d"},
        }
        self.eq_canvases = {
            self.btn_play_start: self.eq_start,
            self.btn_play_end: self.eq_end,
        }
        self._eq_anim_jobs = {}

        # --- Volume Section ---
        self.vol_frame = ctk.CTkFrame(self)
        self.vol_frame.pack(fill="x", padx=15, pady=10)

        header_vol = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        header_vol.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(header_vol, text="Ένταση Ήχου (Volume Control)", font=("Arial", 14, "bold")).pack(side="left")

        self.btn_auto_gain = ctk.CTkButton(header_vol, text="Auto Gain (Peak)", width=110, fg_color="#1f538d", command=self.auto_calculate_gain)
        self.btn_auto_gain.pack(side="right")

        self.lbl_volume = ctk.CTkLabel(self.vol_frame, text="Ένταση: 0.0 dB")
        self.lbl_volume.pack(pady=2)

        self.slider_volume = ctk.CTkSlider(self.vol_frame, from_=-12, to=12, number_of_steps=48, command=self.update_vol_label)
        self.slider_volume.set(0)
        self.slider_volume.pack(fill="x", padx=20, pady=5)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        presets_frame = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        presets_frame.pack(pady=5)
        ctk.CTkButton(presets_frame, text="+1.5 dB", width=65, command=lambda: self.set_gain(1.5)).pack(side="left", padx=5)
        ctk.CTkButton(presets_frame, text="+2.1 dB", width=65, command=lambda: self.set_gain(2.1)).pack(side="left", padx=5)
        ctk.CTkButton(presets_frame, text="Reset (0dB)", width=75, fg_color="#8F4427", hover_color="#612617", command=lambda: self.set_gain(0.0)).pack(side="left", padx=5)

        # --- Fade Controls ---
        self.fade_frame = ctk.CTkFrame(self)
        self.fade_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(self.fade_frame, text="Ομαλή Διαβάθμιση - Fade In / Fade Out (sec)", font=("Arial", 14, "bold")).pack(pady=5)

        grid_fade = ctk.CTkFrame(self.fade_frame, fg_color="transparent")
        grid_fade.pack(pady=5)

        ctk.CTkLabel(grid_fade, text="Fade-In:").grid(row=0, column=0, padx=5, pady=5)
        self.combo_fade_in = ctk.CTkComboBox(grid_fade, width=100, values=["0.0", "0.5", "1.0", "2.0", "3.0", "5.0"])
        self.combo_fade_in.grid(row=0, column=1, padx=10, pady=5)
        self.combo_fade_in.set("0.0")

        ctk.CTkLabel(grid_fade, text="Fade-Out:").grid(row=0, column=2, padx=5, pady=5)
        self.combo_fade_out = ctk.CTkComboBox(grid_fade, width=100, values=["0.0", "0.5", "1.0", "2.0", "3.0", "5.0"])
        self.combo_fade_out.grid(row=0, column=3, padx=10, pady=5)
        self.combo_fade_out.set("0.0")

        # --- Progress Bar ---
        self.progress_bar = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.stop()

        # --- Save / Skip Buttons ---
        self.save_row = ctk.CTkFrame(self, fg_color="transparent")
        self.save_row.pack(fill="x", padx=15, pady=10)

        self.btn_save = ctk.CTkButton(self.save_row, text="Αποθήκευση Νέου Αρχείου (Enter)", fg_color="green", hover_color="darkgreen", height=45, font=("Arial", 15, "bold"), command=self.process_and_save)
        self.btn_save.pack(side="left", fill="x", expand=True)

        self.btn_skip = ctk.CTkButton(self.save_row, text="Παράλειψη ⏭", width=110, height=45, fg_color="#9b7025", hover_color="#46341a", command=self.skip_current)

    def set_controls_state(self, state):
        self.btn_open.configure(state=state)
        self.btn_open_folder.configure(state=state)
        self.btn_save.configure(state=state)
        self.btn_skip.configure(state=state)
        self.btn_settings.configure(state=state)
        self.btn_auto_silence.configure(state=state)
        self.btn_auto_gain.configure(state=state)

    def open_settings_window(self):
        win = ctk.CTkToplevel(self)
        win.title("Ρυθμίσεις")
        win.geometry("460x430")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frame_single = ctk.CTkFrame(win)
        frame_single.pack(fill="x", padx=15, pady=(15, 8))
        ctk.CTkLabel(frame_single, text="Μεμονωμένο τραγούδι", font=("Arial", 14, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        row1 = ctk.CTkFrame(frame_single, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(row1, text="Μετά την αποθήκευση:").pack(side="left")
        single_var = ctk.StringVar(value="Καθαρισμός πεδίων" if self.settings.get("single_after_save") == "reset" else "Διατήρηση ρυθμίσεων")
        single_menu = ctk.CTkOptionMenu(row1, values=["Καθαρισμός πεδίων", "Διατήρηση ρυθμίσεων"], variable=single_var, width=190)
        single_menu.pack(side="right")

        frame_batch = ctk.CTkFrame(win)
        frame_batch.pack(fill="x", padx=15, pady=8)
        ctk.CTkLabel(frame_batch, text="Μαζική Επεξεργασία Φακέλου", font=("Arial", 14, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        row2 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(row2, text="Ανάμεσα σε τραγούδια:").pack(side="left")
        batch_var = ctk.StringVar(value="Καθαρισμός πεδίων" if self.settings.get("batch_after_save") == "reset" else "Διατήρηση ρυθμίσεων")
        batch_menu = ctk.CTkOptionMenu(row2, values=["Καθαρισμός πεδίων", "Διατήρηση ρυθμίσεων"], variable=batch_var, width=190)
        batch_menu.pack(side="right")

        ctk.CTkLabel(frame_batch, text="Φάκελος αποθήκευσης:").pack(anchor="w", padx=10, pady=(8, 2))
        row3 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row3.pack(fill="x", padx=10, pady=(0, 5))
        dir_var = ctk.StringVar(value=self.settings.get("batch_output_dir", ""))
        ent_dir = ctk.CTkEntry(row3, textvariable=dir_var, placeholder_text="(θα ρωτάει κάθε φορά αν το αφήσεις κενό)")
        ent_dir.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def browse_dir():
            chosen = filedialog.askdirectory()
            if chosen:
                dir_var.set(chosen)

        ctk.CTkButton(row3, text="Επιλογή...", width=90, command=browse_dir).pack(side="right")

        row4 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row4.pack(fill="x", padx=10, pady=(5, 10))
        overwrite_var = ctk.BooleanVar(value=self.settings.get("batch_confirm_overwrite", True))
        ctk.CTkCheckBox(row4, text="Επιβεβαίωση πριν την αντικατάσταση υπάρχοντος αρχείου", variable=overwrite_var).pack(anchor="w")

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=15)

        def restore_defaults():
            single_var.set("Καθαρισμός πεδίων")
            batch_var.set("Καθαρισμός πεδίων")
            dir_var.set("")
            overwrite_var.set(True)

        def save_and_close():
            self.settings["single_after_save"] = "reset" if single_var.get() == "Καθαρισμός πεδίων" else "keep"
            self.settings["batch_after_save"] = "reset" if batch_var.get() == "Καθαρισμός πεδίων" else "keep"
            self.settings["batch_output_dir"] = dir_var.get().strip()
            self.settings["batch_confirm_overwrite"] = overwrite_var.get()
            save_settings(self.settings)
            win.destroy()

        ctk.CTkButton(btn_row, text="Επαναφορά προεπιλογών", width=160, fg_color="#8F4427", hover_color="#612617", command=restore_defaults).pack(side="left")
        ctk.CTkButton(btn_row, text="Αποθήκευση & Κλείσιμο", fg_color="green", hover_color="darkgreen", command=save_and_close).pack(side="right")

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg")])
        if not path:
            return

        self.queue_files = []
        self.queue_index = -1
        self.batch_output_dir = None
        self._update_queue_ui()

        self._start_load(path)

    def load_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return

        files = sorted(
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(AUDIO_EXTENSIONS)
        )

        if not files:
            messagebox.showwarning("Προσοχή", "Δεν βρέθηκαν αρχεία ήχου σε αυτόν τον φάκελο.")
            return

        output_dir = self.settings.get("batch_output_dir", "").strip()
        if not output_dir or not os.path.isdir(output_dir):
            chosen = filedialog.askdirectory(title="Επιλογή φακέλου αποθήκευσης για τη μαζική επεξεργασία")
            if not chosen:
                return
            output_dir = chosen

        self.queue_files = files
        self.queue_index = 0
        self.batch_output_dir = output_dir

        self._start_load(self.queue_files[self.queue_index])

    def _start_load(self, path, keep_settings=False):
        self._cancel_play_jobs()
        self.set_controls_state("disabled")
        self.progress_bar.start()
        self.lbl_file.configure(text="Φόρτωση αρχείου...")
        self._update_queue_ui()

        threading.Thread(target=self._load_file_worker, args=(path, keep_settings), daemon=True).start()

    def _load_file_worker(self, path, keep_settings=False):
        try:
            audio = AudioSegment.from_file(path)
            duration_sec = len(audio) / 1000.0
            info = mediainfo(path)
            tags = info.get('TAG', {})
            self.after(0, lambda: self._on_file_loaded(path, audio, duration_sec, tags, keep_settings))
        except Exception as e:
            self.after(0, lambda: self._on_file_load_error(str(e)))

    def _on_file_loaded(self, path, audio, duration_sec, tags, keep_settings=False):
        self.file_path = path
        self.audio = audio
        self.duration_sec = duration_sec
        self.tags = tags

        filename = os.path.basename(path)
        self.lbl_file.configure(text=filename)
        self.lbl_info.configure(text=f"Διάρκεια: {self.duration_sec:.2f}s | Τύπος: {path.split('.')[-1].upper()}")
        self._update_queue_ui()

        if not keep_settings:
            self.ent_start.delete(0, "end")
            self.ent_start.insert(0, "0.00")
            self.ent_end.delete(0, "end")
            self.ent_end.insert(0, "0.00")
            self.slider_volume.set(0)
            self.update_vol_label(0)
            self.combo_fade_in.set("0.0")
            self.combo_fade_out.set("0.0")

        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _on_file_load_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.lbl_file.configure(text="Δεν έχει επιλεγεί αρχείο")
        messagebox.showerror("Σφάλμα", f"Αποτυχία φορτώματος αρχείου:\n{err_msg}")

    def _cancel_play_jobs(self):
        winsound.PlaySound(None, 0)
        for button, job_id in list(self._play_reset_jobs.items()):
            self.after_cancel(job_id)
            self._stop_equalizer(button)
            self._set_playing_ui(button, False)
        self._play_reset_jobs.clear()

    def _in_batch_mode(self):
        return self.queue_index >= 0 and self.queue_index < len(self.queue_files)

    def _update_queue_ui(self):
        if self._in_batch_mode():
            total = len(self.queue_files)
            current_name = os.path.basename(self.queue_files[self.queue_index])
            self.lbl_queue.configure(text=f"🎵 {self.queue_index + 1}/{total} — {current_name}")
            if not self.btn_skip.winfo_ismapped():
                self.btn_skip.pack(side="left", padx=(10, 0))
        else:
            self.lbl_queue.configure(text="")
            if self.btn_skip.winfo_ismapped():
                self.btn_skip.pack_forget()

    def skip_current(self):
        if not self._in_batch_mode():
            return
        self._advance_queue()

    def _advance_queue(self):
        self.queue_index += 1
        if self.queue_index >= len(self.queue_files):
            messagebox.showinfo("Ολοκληρώθηκε", "Η μαζική επεξεργασία ολοκληρώθηκε για όλα τα αρχεία.")
            self.queue_files = []
            self.queue_index = -1
            self.batch_output_dir = None
            self.reset_state()
            return

        keep_settings = self.settings.get("batch_after_save", "reset") == "keep"
        self._start_load(self.queue_files[self.queue_index], keep_settings=keep_settings)

    def reset_state(self, keep_settings=False):
        self._cancel_play_jobs()
        self.file_path = None
        self.audio = None
        self.duration_sec = 0
        self.tags = {}

        self.lbl_file.configure(text="Δεν έχει επιλεγεί αρχείο")
        self.lbl_info.configure(text="Διάρκεια: - | Format: -")
        self._update_queue_ui()

        if not keep_settings:
            self.ent_start.delete(0, "end")
            self.ent_start.insert(0, "0.00")
            self.ent_end.delete(0, "end")
            self.ent_end.insert(0, "0.00")

            self.slider_volume.set(0)
            self.update_vol_label(0)

            self.combo_fade_in.set("0.0")
            self.combo_fade_out.set("0.0")

    def on_closing(self):
        self._cancel_play_jobs()
        for job_id in list(self._eq_anim_jobs.values()):
            self.after_cancel(job_id)
        self.destroy()

    def update_vol_label(self, val):
        self.lbl_volume.configure(text=f"Ένταση: {float(val):+.1f} dB")

    def set_gain(self, gain_val):
        self.slider_volume.set(gain_val)
        self.update_vol_label(gain_val)

    def auto_detect_silence(self):
        if not self.audio:
            messagebox.showwarning("Προσοχή", "Φόρτωσε πρώτα ένα αρχείο ήχου.")
            return

        self.set_controls_state("disabled")
        self.progress_bar.start()

        threading.Thread(target=self._auto_silence_worker, daemon=True).start()

    def _auto_silence_worker(self):
        try:
            start_trim_ms = detect_leading_silence(self.audio, silence_threshold=-50.0)
            end_trim_ms = detect_leading_silence(self.audio.reverse(), silence_threshold=-50.0)
            self.after(0, lambda: self._on_auto_silence_done(start_trim_ms, end_trim_ms))
        except Exception as e:
            self.after(0, lambda: self._on_auto_silence_error(str(e)))

    def _on_auto_silence_done(self, start_ms, end_ms):
        self.ent_start.delete(0, "end")
        self.ent_start.insert(0, f"{start_ms / 1000.0:.2f}")
        self.ent_end.delete(0, "end")
        self.ent_end.insert(0, f"{end_ms / 1000.0:.2f}")

        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _on_auto_silence_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        messagebox.showerror("Σφάλμα", f"Αποτυχία υπολογισμού σιγής:\n{err_msg}")

    def auto_calculate_gain(self):
        if not self.audio:
            messagebox.showwarning("Προσοχή", "Φόρτωσε πρώτα ένα αρχείο ήχου.")
            return

        self.set_controls_state("disabled")
        self.progress_bar.start()

        threading.Thread(target=self._auto_gain_worker, daemon=True).start()

    def _auto_gain_worker(self):
        try:
            if self.audio.max > 0:
                max_possible = self.audio.max_possible_amplitude
                max_gain = 20 * math.log10(max_possible / self.audio.max) - 0.1
                max_gain = min(max(max_gain, -12.0), 12.0)
                gain = round(max_gain, 1)
            else:
                gain = 0.0
            self.after(0, lambda: self._on_auto_gain_done(gain))
        except Exception as e:
            self.after(0, lambda: self._on_auto_gain_error(str(e)))

    def _on_auto_gain_done(self, gain):
        self.set_gain(gain)
        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _on_auto_gain_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        messagebox.showerror("Σφάλμα", f"Αποτυχία υπολογισμού Auto Gain:\n{err_msg}")

    def _create_equalizer(self, parent, width=120, height=32, bars=10):
        canvas = Canvas(parent, width=width, height=height, bg="#242424", highlightthickness=0)
        canvas.eq_bars = []
        bar_width = 8
        gap = 4
        total_width = bars * bar_width + (bars - 1) * gap
        start_x = (width - total_width) / 2
        for i in range(bars):
            x0 = start_x + i * (bar_width + gap)
            x1 = x0 + bar_width
            rect = canvas.create_rectangle(x0, height - 3, x1, height - 1, fill="#3d3d3d", outline="")
            canvas.eq_bars.append((rect, x0, x1))
        return canvas

    def _draw_equalizer(self, canvas, heights, color):
        h = int(canvas["height"])
        for (rect, x0, x1), bar_h in zip(canvas.eq_bars, heights):
            y0 = h - max(2, bar_h)
            canvas.coords(rect, x0, y0, x1, h - 1)
            canvas.itemconfig(rect, fill=color)

    def _reset_equalizer(self, canvas):
        h = int(canvas["height"])
        for rect, x0, x1 in canvas.eq_bars:
            canvas.coords(rect, x0, h - 3, x1, h - 1)
            canvas.itemconfig(rect, fill="#3d3d3d")

    def _animate_equalizer(self, button):
        canvas = self.eq_canvases.get(button)
        if canvas is None:
            return
        try:
            # Ακύρωση τυχόν ήδη προγραμματισμένου loop για αυτό το κουμπί (αποφυγή διπλών loops)
            prev_job = self._eq_anim_jobs.get(button)
            if prev_job is not None:
                self.after_cancel(prev_job)

            color = self.EQ_COLORS[button]["bright"]
            h = int(canvas["height"])
            heights = [random.randint(int(h * 0.2), h - 4) for _ in canvas.eq_bars]
            self._draw_equalizer(canvas, heights, color)
            job_id = self.after(120, lambda: self._animate_equalizer(button))
            self._eq_anim_jobs[button] = job_id
        except Exception:
            pass

    def _stop_equalizer(self, button):
        job_id = self._eq_anim_jobs.pop(button, None)
        if job_id is not None:
            self.after_cancel(job_id)
        canvas = self.eq_canvases.get(button)
        if canvas is not None:
            self._reset_equalizer(canvas)

    def _set_playing_ui(self, button, playing):
        if playing:
            button.configure(text="⏸", fg_color="#c0392b", hover_color="#922b21")
            self._stop_equalizer(button)  # ακύρωση τυχόν προηγούμενου "ορφανού" loop πριν ξεκινήσει νέο
            self._animate_equalizer(button)
        else:
            button.configure(text="▶", fg_color=["#3B8ED0", "#1F6AA5"], hover_color=["#36719F", "#144870"])
            self._stop_equalizer(button)

    def preview_audio_chunk(self, chunk, button=None):
        try:
            winsound.PlaySound(None, 0)
            temp_dir = tempfile.gettempdir()
            temp_file = os.path.join(temp_dir, "trim_preview.wav")
            chunk.export(temp_file, format="wav")

            if button is not None:
                for other_button in list(self._play_reset_jobs.keys()):
                    if other_button is not button:
                        self.after_cancel(self._play_reset_jobs[other_button])
                        self._reset_play_button(other_button)

                existing_job = self._play_reset_jobs.get(button)
                if existing_job is not None:
                    self.after_cancel(existing_job)

                self._set_playing_ui(button, True)
                duration_ms = int(len(chunk)) + 150
                job_id = self.after(duration_ms, lambda: self._reset_play_button(button))
                self._play_reset_jobs[button] = job_id

            winsound.PlaySound(temp_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            if button is not None:
                self._reset_play_button(button)
            messagebox.showerror("Σφάλμα", f"Αδυναμία αναπαραγωγής προεπισκόπησης:\n{e}")

    def _reset_play_button(self, button):
        self._set_playing_ui(button, False)
        self._play_reset_jobs.pop(button, None)

    def preview_start(self):
        if not self.audio:
            return
        try:
            start_sec = float(self.ent_start.get() or 0)
            gain_db = float(self.slider_volume.get())
            fade_in_sec = float(self.combo_fade_in.get() or 0)

            start_ms = int(start_sec * 1000)
            chunk = self.audio[start_ms : start_ms + self.PREVIEW_MS]

            if gain_db != 0:
                chunk += gain_db

            if fade_in_sec > 0:
                fade_ms = min(int(fade_in_sec * 1000), len(chunk))
                chunk = chunk.fade_in(fade_ms)

            self.preview_audio_chunk(chunk, button=self.btn_play_start)
        except ValueError:
            pass

    def preview_end(self):
        if not self.audio:
            return
        try:
            end_sec = float(self.ent_end.get() or 0)
            
            if end_sec >= self.duration_sec or end_sec < 0:
                return

            gain_db = float(self.slider_volume.get())
            fade_out_sec = float(str(self.combo_fade_out.get()).replace("sec", "").strip() or 0)

            end_ms = int((self.duration_sec - end_sec) * 1000)
            chunk = self.audio[max(0, end_ms - self.PREVIEW_MS) : end_ms]

            if gain_db != 0:
                chunk += gain_db

            if fade_out_sec > 0:
                fade_ms = min(int(fade_out_sec * 1000), len(chunk))
                chunk = chunk.fade_out(fade_ms)

            self.preview_audio_chunk(chunk, button=self.btn_play_end)
        except ValueError:
            pass

    def process_and_save(self):
        if not self.audio:
            messagebox.showwarning("Προσοχή", "Παρακαλώ άνοιξε πρώτα ένα αρχείο ήχου.")
            return

        try:
            trim_start_sec = float(self.ent_start.get() or 0)
            trim_end_sec = float(self.ent_end.get() or 0)
            gain_db = float(self.slider_volume.get())

            fade_in_sec = float(str(self.combo_fade_in.get()).replace("sec", "").strip() or 0)
            fade_out_sec = float(str(self.combo_fade_out.get()).replace("sec", "").strip() or 0)

            if trim_start_sec < 0 or trim_end_sec < 0:
                messagebox.showerror("Σφάλμα", "Οι τιμές περικοπής δεν μπορούν να είναι αρνητικές.")
                return

            if (trim_start_sec + trim_end_sec) >= self.duration_sec:
                messagebox.showerror("Σφάλμα", f"Το άθροισμα περικοπής ({trim_start_sec + trim_end_sec:.2f}s) είναι μεγαλύτερο ή ίσο με τη συνολική διάρκεια του τραγουδιού ({self.duration_sec:.2f}s).")
                return

            start_ms = int(trim_start_sec * 1000)
            end_ms = int((self.duration_sec - trim_end_sec) * 1000)

            if self._in_batch_mode():
                initial_file = os.path.basename(self.file_path)
                out_path = os.path.join(self.batch_output_dir, initial_file)

                if os.path.exists(out_path) and self.settings.get("batch_confirm_overwrite", True):
                    proceed = messagebox.askyesno(
                        "Το αρχείο υπάρχει ήδη",
                        f"Το αρχείο '{initial_file}' υπάρχει ήδη στον φάκελο αποθήκευσης.\nΝα αντικατασταθεί;"
                    )
                    if not proceed:
                        return
            else:
                initial_dir = os.path.dirname(self.file_path) if self.file_path else None
                initial_file = os.path.basename(self.file_path) if self.file_path else ""
                ext = os.path.splitext(initial_file)[1] if initial_file else ".mp3"

                out_path = filedialog.asksaveasfilename(
                    initialdir=initial_dir,
                    initialfile=initial_file,
                    defaultextension=ext,
                    filetypes=[("Audio Files", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg"), ("MP3 File", "*.mp3"), ("WAV File", "*.wav")]
                )
                if not out_path:
                    return

            self.set_controls_state("disabled")
            self.progress_bar.start()
            self.btn_save.configure(text="Επεξεργασία & Αποθήκευση...")

            threading.Thread(
                target=self._export_worker,
                args=(out_path, start_ms, end_ms, gain_db, fade_in_sec, fade_out_sec),
                daemon=True
            ).start()

        except ValueError:
            messagebox.showerror("Σφάλμα", "Παρακαλώ εισάγετε έγκυρους αριθμούς στα πεδία περικοπής / fade.")
        except Exception as e:
            messagebox.showerror("Σφάλμα", f"Προέκυψε σφάλμα:\n{e}")

    def _export_worker(self, out_path, start_ms, end_ms, gain_db, fade_in_sec, fade_out_sec):
        try:
            processed = self.audio[start_ms:end_ms]

            if gain_db != 0:
                processed += gain_db

            if fade_in_sec > 0:
                processed = processed.fade_in(int(fade_in_sec * 1000))

            if fade_out_sec > 0:
                processed = processed.fade_out(int(fade_out_sec * 1000))

            fmt = out_path.split('.')[-1].lower()
            
            export_kwargs = {}
            if fmt == "mp3":
                export_kwargs["bitrate"] = "320k"

            if fmt == "mp3" and self.tags:
                processed.export(out_path, format=fmt, tags=self.tags, **export_kwargs)
            else:
                processed.export(out_path, format=fmt, **export_kwargs)

            self.after(0, self._on_export_success)
        except Exception as e:
            self.after(0, lambda: self._on_export_error(str(e)))

    def _on_export_success(self):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.btn_save.configure(text="Αποθήκευση Νέου Αρχείου (Enter)")

        if self._in_batch_mode():
            self._advance_queue()
        else:
            messagebox.showinfo("Επιτυχία", "Το αρχείο αποθηκεύτηκε επιτυχώς!")
            keep_settings = self.settings.get("single_after_save", "reset") == "keep"
            self.reset_state(keep_settings=keep_settings)

    def _on_export_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.btn_save.configure(text="Αποθήκευση Νέου Αρχείου (Enter)")
        messagebox.showerror("Σφάλμα", f"Αποτυχία κατά την αποθήκευση:\n{err_msg}")

if __name__ == "__main__":
    app = AudioTrimmerApp()
    app.mainloop()