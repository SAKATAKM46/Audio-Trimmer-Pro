import os
import sys
import math
import tempfile
import winsound
import threading
import subprocess
import socket
import json
import customtkinter as ctk
from tkinter import filedialog, messagebox, Canvas, Menu

# --- Προαιρετική υποστήριξη Drag & Drop (χρειάζεται: pip install tkinterdnd2) ---
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# --- 1. Εξαφάνιση Μαύρου Παραθύρου Τερματικού (Windows Subprocess Fix) ---
if sys.platform == "win32":
    _orig_popen = subprocess.Popen
    def _silent_popen(*args, **kwargs):
        creationflags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = creationflags | subprocess.CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)
    subprocess.Popen = _silent_popen

# ======================================================================
#  Μία μόνο διεργασία («single instance») — αν είναι ήδη ανοιχτή η εφαρμογή, το
#  διπλό-κλικ στο εικονίδιο δεν ανοίγει δεύτερο παράθυρο, απλά φέρνει το υπάρχον
#  μπροστά. Χρησιμοποιούμε ένα τοπικό TCP socket (127.0.0.1) σαν «κλειδαριά»: αν
#  η θύρα είναι ήδη δεσμευμένη, σημαίνει ότι τρέχει ήδη μια διεργασία — της
#  στέλνουμε ένα μικρό μήνυμα και τερματίζουμε αμέσως, ΠΡΙΝ ανοίξουμε δικό μας
#  παράθυρο. Σε αντίθεση με ένα lock-file, ένα TCP socket ΔΕΝ μένει «κολλημένο»
#  αν η εφαρμογή κρασάρει — το λειτουργικό το ελευθερώνει αυτόματα.
# ======================================================================
SINGLE_INSTANCE_PORT = 47823  # διαφορετική θύρα από τα άλλα προγράμματα (π.χ. YouTube Downloader)


def acquire_single_instance_lock():
    """ Επιστρέφει (is_primary, sock).
    - is_primary=True, sock=<socket>: είμαστε η μοναδική διεργασία -> άνοιξε κανονικά
      παράθυρο (κράτα το sock ζωντανό όσο τρέχει η εφαρμογή).
    - is_primary=False, sock=None: υπάρχει ήδη ανοιχτή η εφαρμογή και ειδοποιήθηκε να
      έρθει μπροστά -> ΜΗΝ ανοίξεις παράθυρο, τερμάτισε αμέσως.
    Αν δεν μπορέσαμε να ελέγξουμε καθόλου (π.χ. μπλοκαρισμένα sockets από
    firewall/antivirus), προχωράμε σαν να είμαστε primary ώστε να ΜΗΝ εμποδίσουμε
    ποτέ οριστικά το άνοιγμα της εφαρμογής. """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # ΠΡΟΣΟΧΗ: ΕΠΙΤΗΔΕΣ δεν βάζουμε SO_REUSEADDR εδώ (βλ. σχόλιο στην αδερφή
        # εφαρμογή YouTube Downloader) — στα Windows θα επέτρεπε σε άλλη διεργασία
        # να κάνει bind() στην ΙΔΙΑ θύρα ενώ μια πρώτη τη «ακούει» ήδη.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(5)
        return True, s
    except OSError:
        try:
            client = socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=1.5)
            client.sendall(b"SHOW")
            client.close()
            return False, None
        except Exception:
            # Η θύρα φαίνεται δεσμευμένη αλλά δεν απαντάει κανείς (πιθανό «στοιχειωμένο»
            # υπόλειμμα) -> ανοίγουμε κανονικά αντί να μπλοκάρουμε την εφαρμογή.
            return True, None
    except Exception:
        return True, None

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

SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.json")  # παλιά (λανθασμένη) θέση - κρατιέται μόνο για migration


def _get_user_data_dir():
    """Φάκελος ρυθμίσεων στον χρήστη (%APPDATA% στα Windows), ώστε να δουλεύει
    η αποθήκευση ρυθμίσεων ΑΝΕΞΑΡΤΗΤΑ από το πού είναι εγκατεστημένο το πρόγραμμα
    (π.χ. C:\\Program Files, όπου τα Windows μπλοκάρουν εγγραφές χωρίς admin δικαιώματα)."""
    try:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        else:
            base = os.path.join(os.path.expanduser("~"), ".config")
        app_dir = os.path.join(base, "AudioTrimmerPro")
        os.makedirs(app_dir, exist_ok=True)
        return app_dir
    except Exception:
        return SCRIPT_DIR  # ελάχιστο fallback, σπάνια θα χρειαστεί


_USER_DATA_DIR = _get_user_data_dir()
_NEW_SETTINGS_PATH = os.path.join(_USER_DATA_DIR, "settings.json")

# --- Μία φορά migration: αν υπάρχει παλιό settings.json δίπλα στο exe (από
# προηγούμενη έκδοση) και δεν υπάρχει ακόμα νέο στο %APPDATA%, το αντιγράφουμε ---
if not os.path.exists(_NEW_SETTINGS_PATH) and os.path.exists(SETTINGS_PATH):
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as _f:
            _old_data = _f.read()
        with open(_NEW_SETTINGS_PATH, "w", encoding="utf-8") as _f:
            _f.write(_old_data)
    except Exception:
        pass

SETTINGS_PATH = _NEW_SETTINGS_PATH

DEFAULT_SETTINGS = {
    "single_after_save": "reset",       # "reset" | "keep"
    "batch_after_save": "reset",        # "reset" | "keep"
    "batch_output_dir": "",             # "" -> ρωτάει φάκελο στην έναρξη μαζικής επεξεργασίας
    "batch_confirm_overwrite": True,    # True -> ρωτάει πριν αντικαταστήσει υπάρχον αρχείο
    "recent_files": [],                 # Λίστα με τα πρόσφατα αρχεία
    "max_recent_files": 8,              # Μέγιστος αριθμός πρόσφατων αρχείων
    "language": "el",                   # Γλώσσα: "el" (Ελληνικά), "en" (English)
    "export_format": "MP3",
    "bitrate": "320k",
    "sample_rate": "Original",
    "fade_curve": "Νορμάλ (Linear)"
}

_TRANSLATIONS = {
    "el": {
        "app_title": "Audio Trimmer Pro v2.6",
        "open": "Άνοιγμα (Ctrl+O)",
        "open_folder": "Άνοιγμα Φακέλου",
        "auto_trim": "Auto Trim Κενών",
        "auto_gain": "Auto Gain (Peak)",
        "save": "Αποθήκευση Νέου Αρχείου (Enter)",
        "saving": "Επεξεργασία & Αποθήκευση...",
        "skip": "Παράλειψη ⏭",
        "no_file": "Δεν έχει επιλεγεί αρχείο",
        "loading": "Φόρτωση αρχείου...",
        "error": "Σφάλμα",
        "warning": "Προσοχή",
        "success": "Επιτυχία",
        "file_saved": "Το αρχείο αποθηκεύτηκε επιτυχώς!",
        "load_file_first": "Παρακαλώ άνοιξε πρώτα ένα αρχείο ήχου.",
        "invalid_numbers": "Παρακαλώ εισάγετε έγκυρους αριθμούς στα πεδία περικοπής / fade.",
        "negative_trim": "Οι τιμές περικοπής δεν μπορούν να είναι αρνητικές.",
        "batch_complete": "Η μαζική επεξεργασία ολοκληρώθηκε για όλα τα αρχεία.",
        "file_exists": "Το αρχείο υπάρχει ήδη",
        "no_audio_files": "Δεν βρέθηκαν αρχεία ήχου σε αυτόν τον φάκελο.",
        "large_file_title": "Μεγάλο Αρχείο",
        "large_file_msg": "Το αρχείο είναι {0:.1f} MB. Η φόρτωση μπορεί να διαρκέσει και να καταναλώσει πολλή μνήμη.\nΝα συνεχίσω;",
        "trim_section_title": "Περικοπή & Αυτόματα Κενά",
        "start_trim_label": "Από Αρχή (sec):",
        "end_trim_label": "Από Τέλος (sec):",
        "volume_section_title": "Ένταση Ήχου (Volume Control)",
        "volume_label": "Ένταση: {0:+.1f} dB",
        "reset_volume": "Reset (0dB)",
        "fade_section_title": "Ομαλή Διαβάθμιση - Fade In / Fade Out (sec)",
        "fade_in_label": "Fade-In:",
        "fade_out_label": "Fade-Out:",
        "settings_title": "Ρυθμίσεις",
        "single_song": "Μεμονωμένο τραγούδι",
        "batch_processing": "Μαζική Επεξεργασία Φακέλου",
        "after_save": "Μετά την αποθήκευση:",
        "between_songs": "Ανάμεσα σε τραγούδια:",
        "clear_fields": "Καθαρισμός πεδίων",
        "keep_settings": "Διατήρηση ρυθμίσεων",
        "output_folder": "Φάκελος αποθήκευσης:",
        "output_folder_placeholder": "(θα ρωτάει κάθε φορά αν το αφήσεις κενό)",
        "browse": "Επιλογή...",
        "confirm_overwrite": "Επιβεβαίωση πριν την αντικατάσταση υπάρχοντος αρχείου",
        "general_settings": "Γενικές Ρυθμίσεις",
        "language_label": "Γλώσσα / Language:",
        "restore_defaults": "Επαναφορά προεπιλογών",
        "save_close": "Αποθήκευση & Κλείσιμο",
        "greek_lang": "Ελληνικά (EL)",
        "english_lang": "English (EN)",
        "duration_format": "Διάρκεια: {0:.2f}s | Τύπος: {1}",
        "duration_unknown": "Διάρκεια: - | Format: -",
        "trim_start_large": "Η τιμή 'Από Αρχή' ({0:.2f}s) είναι μεγαλύτερη ή ίση με τη συνολική διάρκεια ({1:.2f}s).",
        "trim_end_large": "Η τιμή 'Από Τέλος' ({0:.2f}s) είναι μεγαλύτερη ή ίση με το υπόλοιπο της διάρκειας μετά την περικοπή αρχής ({1:.2f}s).",
        "trim_sum_large": "Το άθροισμα περικοπής ({0:.2f}s) είναι μεγαλύτερο ή ίσο με τη συνολική διάρκεια του τραγουδιού ({1:.2f}s).",
        "invalid_output_dir": "Δεν υπάρχει έγκυρο αρχείο ή φάκελος αποθήκευσης.",
        "file_exists_msg": "Το αρχείο '{0}' υπάρχει ήδη στον φάκελο αποθήκευσης.\nΝα αντικατασταθεί;",
        "select_output_dir_batch": "Επιλογή φακέλου αποθήκευσης για τη μαζική επεξεργασία",
        "load_file_error": "Αποτυχία φορτώματος αρχείου:\n{0}",
        "auto_silence_error": "Αποτυχία υπολογισμού σιγής:\n{0}",
        "auto_gain_error": "Αποτυχία υπολογισμού Auto Gain:\n{0}",
        "preview_error": "Αδυναμία αναπαραγωγής προεπισκόπησης:\n{0}",
        "export_error": "Αποτυχία κατά την αποθήκευση:\n{0}",
        "audio_files_dialog": "Αρχεία Ήχου",
        "all_audio_dialog": "Όλα τα αρχεία ήχου",
        "mp3_file_dialog": "Αρχείο MP3",
        "wav_file_dialog": "Αρχείο WAV",
        "completed": "Ολοκληρώθηκε",
        "song_info_title": "Στοιχεία Τραγουδιού",
        "title_label": "Τίτλος:",
        "artist_label": "Καλλιτέχνης:",
        "album_label": "Άλμπουμ:",
        "cancel": "Ακύρωση",
        "dialog_save": "Αποθήκευση",
        "cut_menu": "Αποκοπή",
        "copy_menu": "Αντιγραφή",
        "paste_menu": "Επικόλληση",
        "select_all_menu": "Επιλογή Όλων",
    },
    "en": {
        "app_title": "Audio Trimmer Pro v2.6",
        "open": "Open (Ctrl+O)",
        "open_folder": "Open Folder",
        "auto_trim": "Auto Trim Silence",
        "auto_gain": "Auto Gain (Peak)",
        "save": "Save New File (Enter)",
        "saving": "Processing & Saving...",
        "skip": "Skip ⏭",
        "no_file": "No file selected",
        "loading": "Loading file...",
        "error": "Error",
        "warning": "Warning",
        "success": "Success",
        "file_saved": "File saved successfully!",
        "load_file_first": "Please open an audio file first.",
        "invalid_numbers": "Please enter valid numbers in trim / fade fields.",
        "negative_trim": "Trim values cannot be negative.",
        "batch_complete": "Batch processing completed for all files.",
        "file_exists": "File already exists",
        "no_audio_files": "No audio files found in this folder.",
        "large_file_title": "Large File",
        "large_file_msg": "The file is {0:.1f} MB. Loading may take a long time and consume a lot of memory.\nDo you want to continue?",
        "trim_section_title": "Trim & Auto Silence",
        "start_trim_label": "From Start (sec):",
        "end_trim_label": "From End (sec):",
        "volume_section_title": "Volume Control",
        "volume_label": "Volume: {0:+.1f} dB",
        "reset_volume": "Reset (0dB)",
        "fade_section_title": "Fade In / Fade Out (sec)",
        "fade_in_label": "Fade-In:",
        "fade_out_label": "Fade-Out:",
        "settings_title": "Settings",
        "single_song": "Single Song",
        "batch_processing": "Batch Folder Processing",
        "after_save": "After saving:",
        "between_songs": "Between songs:",
        "clear_fields": "Clear fields",
        "keep_settings": "Keep settings",
        "output_folder": "Output folder:",
        "output_folder_placeholder": "(will ask every time if left empty)",
        "browse": "Browse...",
        "confirm_overwrite": "Confirm before overwriting existing file",
        "general_settings": "General Settings",
        "language_label": "Language / Γλώσσα:",
        "restore_defaults": "Restore Defaults",
        "save_close": "Save & Close",
        "greek_lang": "Ελληνικά (EL)",
        "english_lang": "English (EN)",
        "duration_format": "Duration: {0:.2f}s | Format: {1}",
        "duration_unknown": "Duration: - | Format: -",
        "trim_start_large": "The 'From Start' value ({0:.2f}s) is greater than or equal to the total duration ({1:.2f}s).",
        "trim_end_large": "The 'From End' value ({0:.2f}s) is greater than or equal to the remaining duration after starting trim ({1:.2f}s).",
        "trim_sum_large": "The total trim ({0:.2f}s) is greater than or equal to the song's total duration ({1:.2f}s).",
        "invalid_output_dir": "There is no valid file or output folder.",
        "file_exists_msg": "The file '{0}' already exists in the output folder.\nReplace it?",
        "select_output_dir_batch": "Select output folder for batch processing",
        "load_file_error": "Failed to load file:\n{0}",
        "auto_silence_error": "Failed to calculate silence:\n{0}",
        "auto_gain_error": "Failed to calculate Auto Gain:\n{0}",
        "preview_error": "Unable to play preview:\n{0}",
        "export_error": "Failed during saving:\n{0}",
        "audio_files_dialog": "Audio Files",
        "all_audio_dialog": "All Audio Files",
        "mp3_file_dialog": "MP3 File",
        "wav_file_dialog": "WAV File",
        "completed": "Completed",
        "song_info_title": "Song Info",
        "title_label": "Title:",
        "artist_label": "Artist:",
        "album_label": "Album:",
        "cancel": "Cancel",
        "dialog_save": "Save",
        "cut_menu": "Cut",
        "copy_menu": "Copy",
        "paste_menu": "Paste",
        "select_all_menu": "Select All",
    }
}

_CURRENT_LANG = "el"


def set_language(lang):
    global _CURRENT_LANG
    if lang in _TRANSLATIONS:
        _CURRENT_LANG = lang


def _(key, *args):
    translations = _TRANSLATIONS.get(_CURRENT_LANG, _TRANSLATIONS["el"])
    text = translations.get(key, key)
    if args:
        try:
            return text.format(*args)
        except Exception:
            return text
    return text


class HistoryManager:
    def __init__(self, max_size=50):
        self.history = []
        self.future = []
        self.max_size = max_size
    
    def push(self, state):
        if self.history and self.history[-1] == state:
            return
        self.history.append(state)
        if len(self.history) > self.max_size:
            self.history.pop(0)
        self.future.clear()
    
    def undo(self):
        if not self.history:
            return None
        state = self.history.pop()
        self.future.append(state)
        if self.history:
            return self.history[-1]
        return None
    
    def redo(self):
        if not self.future:
            return None
        state = self.future.pop()
        self.history.append(state)
        return state
    
    def can_undo(self):
        return len(self.history) > 1
    
    def can_redo(self):
        return len(self.future) > 0


AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg")


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            set_language(merged.get("language", "el"))
            return merged
        except Exception:
            pass
    set_language("el")
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

def apply_safe_gain(segment: AudioSegment, requested_db: float) -> AudioSegment:
    """Εφαρμόζει gain εμποδίζοντας την παραμόρφωση (Anti-Clipping)."""
    if requested_db == 0:
        return segment
    
    max_peak = segment.max_dBFS if segment.max_dBFS is not None else 0.0
    headroom = 0.0 - max_peak  # Πόσα dB αντέχει μέχρι το 0 dBFS
    
    # Περιορισμός του gain για αποφυγή clipping
    safe_db = min(requested_db, max(0.0, headroom))
    return segment.apply_gain(safe_db)


def apply_custom_fade(segment: AudioSegment, duration_ms: int, mode: str = "in", curve_type: str = "Νορμάλ (Linear)") -> AudioSegment:
    """Εφαρμόζει Fade In / Fade Out με γραμμικές, ήπιες ή επιθετικές καμπύλες."""
    if duration_ms <= 0 or len(segment) == 0:
        return segment

    duration_ms = min(duration_ms, len(segment))

    # Γραμμική καμπύλη (PyDub Default)
    ct_lower = curve_type.lower()
    if "linear" in ct_lower or "νορμάλ" in ct_lower:
        return segment.fade_in(duration_ms) if mode == "in" else segment.fade_out(duration_ms)

    # Για Ήπια (Logarithmic) & Επιθετική (Exponential) καμπύλη
    step_ms = 20
    steps = max(1, duration_ms // step_ms)
    
    fade_slice = segment[:duration_ms] if mode == "in" else segment[-duration_ms:]
    rest_slice = segment[duration_ms:] if mode == "in" else segment[:-duration_ms]

    processed_chunks = []
    chunk_len = len(fade_slice) / steps

    for i in range(steps):
        t = (i + 1) / steps  # Ποσοστό (0.0 έως 1.0)
        
        if "ήπια" in ct_lower or "soft" in ct_lower or "log" in ct_lower:
            factor = math.sin(t * (math.pi / 2))
        elif "επιθετική" in ct_lower or "aggressive" in ct_lower or "exp" in ct_lower:
            factor = t ** 3
        else:
            factor = t

        if mode == "out":
            factor = 1.0 - factor

        gain_db = (1.0 - factor) * -60.0 if factor > 0.001 else -120.0
        
        start_p = int(i * chunk_len)
        end_p = int((i + 1) * chunk_len)
        chunk = fade_slice[start_p:end_p].apply_gain(-abs(gain_db))
        processed_chunks.append(chunk)

    faded_part = sum(processed_chunks)
    return (faded_part + rest_slice) if mode == "in" else (rest_slice + faded_part)


def is_lossless_copy_eligible(start_ms, end_ms, total_ms, gain_db, fade_in_ms, fade_out_ms, in_ext, out_ext, sample_rate="Original"):
    """Ελέγχει αν μπορούμε να κάνουμε direct stream copy (χωρίς re-encoding)."""
    no_effects = (gain_db == 0.0 and fade_in_ms == 0 and fade_out_ms == 0)
    same_format = (in_ext.lower().replace('.', '') == out_ext.lower().replace('.', ''))
    is_trimmed = (start_ms > 0 or end_ms < total_ms)
    no_resample = (not sample_rate) or (str(sample_rate) == "Original")
    return no_effects and same_format and is_trimmed and no_resample

def _tag_get(tags, *keys):
    """Case-insensitive αναζήτηση σε dict με tags (π.χ. 'title', 'TITLE', 'Title')."""
    if not tags:
        return ""
    lower_map = {str(k).lower(): v for k, v in tags.items()}
    for key in keys:
        val = lower_map.get(key.lower())
        if val:
            return str(val).strip()
    return ""


_TITLE_ARTIST_SEPARATORS = [" - ", " – ", " — ", " | ", "_-_"]


def guess_clean_tags(raw_tags, fallback_filename=""):
    """
    Προσπαθεί να ξεχωρίσει Καλλιτέχνη/Τίτλο/Άλμπουμ όταν είναι μπερδεμένα.
    Π.χ. αν το title field περιέχει 'Καλλιτέχνης - Τίτλος' αλλά ο artist είναι κενός.
    Δεν αλλάζει τίποτα αν τα δεδομένα είναι ήδη σωστά διαχωρισμένα.
    """
    title = _tag_get(raw_tags, "title")
    artist = _tag_get(raw_tags, "artist")
    album = _tag_get(raw_tags, "album")

    # Αν λείπει ο τίτλος εντελώς, χρησιμοποίησε το filename σαν βάση
    if not title and fallback_filename:
        title = os.path.splitext(os.path.basename(fallback_filename))[0]

    # Αν ο artist είναι κενός αλλά ο τίτλος περιέχει κάποιο διαχωριστικό,
    # υπόθεσε ότι είναι σε μορφή "Καλλιτέχνης - Τίτλος"
    if title and not artist:
        for sep in _TITLE_ARTIST_SEPARATORS:
            if sep in title:
                left, right = title.split(sep, 1)
                left, right = left.strip(), right.strip()
                if left and right:
                    artist, title = left, right
                    break

    return {"title": title, "artist": artist, "album": album}


if DND_AVAILABLE:
    class _AppBase(ctk.CTk, TkinterDnD.DnDWrapper):
        """Βάση παραθύρου με προαιρετική υποστήριξη drag & drop (tkinterdnd2)."""
        pass
else:
    class _AppBase(ctk.CTk):
        pass


class AudioTrimmerApp(_AppBase):
    def __init__(self, single_instance_sock=None):
        super().__init__()

        if DND_AVAILABLE:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:
                pass

        self.title("Audio Trimmer Pro v2.6")
        self.geometry("580x620")
        self.resizable(True, True)

        self._single_instance_sock = single_instance_sock
        if single_instance_sock is not None:
            self._start_single_instance_listener(single_instance_sock)

        try:
            icon_path = os.path.join(SCRIPT_DIR, "app_icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

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

        # --- History for Undo/Redo ---
        self._history = HistoryManager(max_size=30)
        self._updating_from_history = False

        # --- Keyboard Shortcuts ---
        self.bind("<Control-o>", lambda e: self.load_file())
        self.bind("<Return>", lambda e: self.process_and_save())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.redo())
        self.bind("<Control-Shift-Z>", lambda e: self.redo())

        # --- File Selection ---
        self.file_frame = ctk.CTkFrame(self)
        self.file_frame.pack(fill="x", padx=15, pady=10)

        self.btn_open = ctk.CTkButton(self.file_frame, text="", command=self.load_file)
        self.btn_open.pack(side="left", padx=(10, 5), pady=10)

        self.btn_open_folder = ctk.CTkButton(self.file_frame, text="", width=110, fg_color="#8F4427", hover_color="#612617", command=self.load_folder)
        self.btn_open_folder.pack(side="left", padx=5, pady=10)

        self.btn_settings = ctk.CTkButton(self.file_frame, text="⚙", width=32, fg_color="#6A2C2C", hover_color="#760e0e", command=self.open_settings_window)
        self.btn_settings.pack(side="right", padx=(5, 10), pady=10)

        self.lbl_file = ctk.CTkLabel(self.file_frame, text="", anchor="w")
        self.lbl_file.pack(side="left", fill="x", expand=True, padx=10)

        # --- Queue Progress ---
        self.lbl_queue = ctk.CTkLabel(self, text="", text_color="#5cb85c", font=("Arial", 12, "bold"))
        self.lbl_queue.pack(pady=(0, 2))

        self.lbl_info = ctk.CTkLabel(self, text="", text_color="gray")
        self.lbl_info.pack(pady=2)

        # --- Trimming & Auto Silence Section ---
        self.trim_frame = ctk.CTkFrame(self)
        self.trim_frame.pack(fill="x", padx=15, pady=10)

        header_trim = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        header_trim.pack(fill="x", padx=10, pady=5)
        self.lbl_trim_header = ctk.CTkLabel(header_trim, text="", font=("Arial", 14, "bold"))
        self.lbl_trim_header.pack(side="left")
        
        self.btn_auto_silence = ctk.CTkButton(header_trim, text="", width=110, fg_color="#1f538d", command=self.auto_detect_silence)
        self.btn_auto_silence.pack(side="right")

        grid_trim = ctk.CTkFrame(self.trim_frame, fg_color="transparent")
        grid_trim.pack(pady=5)

        # --- Start Trim (Sec & Dec) ---
        self.lbl_start_trim = ctk.CTkLabel(grid_trim, text=_("start_trim_label"))
        self.lbl_start_trim.grid(row=0, column=0, padx=(5, 2), pady=5)

        start_frame = ctk.CTkFrame(grid_trim, fg_color="transparent")
        start_frame.grid(row=0, column=1, padx=2, pady=5)

        self.ent_start_sec = ctk.CTkEntry(start_frame, width=45, placeholder_text="0", justify="center")
        self.ent_start_sec.pack(side="left")
        self.ent_start_sec.insert(0, "0")
        self.ent_start_sec.bind("<KeyRelease>", self._on_control_change)

        ctk.CTkLabel(start_frame, text=".", font=("Arial", 14, "bold")).pack(side="left", padx=1)

        self.ent_start_dec = ctk.CTkEntry(start_frame, width=35, placeholder_text="00", justify="center")
        self.ent_start_dec.pack(side="left")
        self.ent_start_dec.insert(0, "00")
        self.ent_start_dec.bind("<KeyRelease>", self._on_control_change)

        self.btn_play_start = ctk.CTkButton(grid_trim, text="▶", width=35, command=self.preview_start)
        self.btn_play_start.grid(row=0, column=2, padx=5, pady=5)
        self.eq_start = self._create_equalizer(grid_trim)
        self.eq_start.grid(row=0, column=3, padx=(5, 5), pady=5)

        # --- End Trim (Sec & Dec) ---
        self.lbl_end_trim = ctk.CTkLabel(grid_trim, text=_("end_trim_label"))
        self.lbl_end_trim.grid(row=1, column=0, padx=(5, 2), pady=5)

        end_frame = ctk.CTkFrame(grid_trim, fg_color="transparent")
        end_frame.grid(row=1, column=1, padx=2, pady=5)

        self.ent_end_sec = ctk.CTkEntry(end_frame, width=45, placeholder_text="0", justify="center")
        self.ent_end_sec.pack(side="left")
        self.ent_end_sec.insert(0, "0")
        self.ent_end_sec.bind("<KeyRelease>", self._on_control_change)

        ctk.CTkLabel(end_frame, text=".", font=("Arial", 14, "bold")).pack(side="left", padx=1)

        self.ent_end_dec = ctk.CTkEntry(end_frame, width=35, placeholder_text="00", justify="center")
        self.ent_end_dec.pack(side="left")
        self.ent_end_dec.insert(0, "00")
        self.ent_end_dec.bind("<KeyRelease>", self._on_control_change)

        self.btn_play_end = ctk.CTkButton(grid_trim, text="▶", width=35, command=self.preview_end)
        self.btn_play_end.grid(row=1, column=2, padx=5, pady=5)
        self.eq_end = self._create_equalizer(grid_trim)
        self.eq_end.grid(row=1, column=3, padx=(5, 5), pady=5)

        self.EQ_COLORS = {
            self.btn_play_start: {"bright": "#2ecc71", "dim": "#3d3d3d"},
            self.btn_play_end: {"bright": "#2ecc71", "dim": "#3d3d3d"},
        }
        self.eq_canvases = {
            self.btn_play_start: self.eq_start,
            self.btn_play_end: self.eq_end,
        }
        self._eq_anim_jobs = {}
        self._eq_profiles = {}       # button -> λίστα με πραγματικά επίπεδα έντασης ανά χρονικό παράθυρο
        self._eq_profile_idx = {}    # button -> τρέχων δείκτης θέσης μέσα στο profile

        # --- Volume Section ---
        self.vol_frame = ctk.CTkFrame(self)
        self.vol_frame.pack(fill="x", padx=15, pady=10)

        header_vol = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        header_vol.pack(fill="x", padx=10, pady=5)
        self.lbl_vol_header = ctk.CTkLabel(header_vol, text="", font=("Arial", 14, "bold"))
        self.lbl_vol_header.pack(side="left")

        self.btn_auto_gain = ctk.CTkButton(header_vol, text="", width=110, fg_color="#1f538d", command=self.auto_calculate_gain)
        self.btn_auto_gain.pack(side="right")

        self.lbl_volume = ctk.CTkLabel(self.vol_frame, text="")
        self.lbl_volume.pack(pady=2)

        self.slider_volume = ctk.CTkSlider(
            self.vol_frame, from_=-12, to=12, number_of_steps=48,
            command=self.update_vol_label
        )
        self.slider_volume.set(0)
        self.slider_volume.pack(fill="x", padx=20, pady=5)
        self.slider_volume.bind("<ButtonRelease-1>", self._on_control_change)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        presets_frame = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        presets_frame.pack(pady=5)
        self.btn_preset1 = ctk.CTkButton(presets_frame, text="+1.5 dB", width=65, command=lambda: self.set_gain(1.5))
        self.btn_preset1.pack(side="left", padx=5)
        self.btn_preset2 = ctk.CTkButton(presets_frame, text="+2.1 dB", width=65, command=lambda: self.set_gain(2.1))
        self.btn_preset2.pack(side="left", padx=5)
        self.btn_reset_vol = ctk.CTkButton(presets_frame, text="", width=75, fg_color="#8F4427", hover_color="#612617", command=lambda: self.set_gain(0.0))
        self.btn_reset_vol.pack(side="left", padx=5)

        # --- Fade Controls ---
        self.fade_frame = ctk.CTkFrame(self)
        self.fade_frame.pack(fill="x", padx=15, pady=10)

        self.lbl_fade_header = ctk.CTkLabel(self.fade_frame, text="", font=("Arial", 14, "bold"))
        self.lbl_fade_header.pack(pady=5)

        grid_fade = ctk.CTkFrame(self.fade_frame, fg_color="transparent")
        grid_fade.pack(pady=5)

        self.lbl_fade_in = ctk.CTkLabel(grid_fade, text="")
        self.lbl_fade_in.grid(row=0, column=0, padx=5, pady=5)
        self.combo_fade_in = ctk.CTkComboBox(grid_fade, width=100, values=["0.0", "0.5", "1.0", "2.0", "3.0", "5.0"])
        self.combo_fade_in.grid(row=0, column=1, padx=10, pady=5)
        self.combo_fade_in.set("0.0")
        self.combo_fade_in.bind("<<ComboboxSelected>>", self._on_control_change)

        self.lbl_fade_out = ctk.CTkLabel(grid_fade, text="")
        self.lbl_fade_out.grid(row=0, column=2, padx=5, pady=5)
        self.combo_fade_out = ctk.CTkComboBox(grid_fade, width=100, values=["0.0", "0.5", "1.0", "2.0", "3.0", "5.0"])
        self.combo_fade_out.grid(row=0, column=3, padx=10, pady=5)
        self.combo_fade_out.set("0.0")
        self.combo_fade_out.bind("<<ComboboxSelected>>", self._on_control_change)

        # --- Fade Curve Selector ---
        self.lbl_fade_curve = ctk.CTkLabel(grid_fade, text="Καμπύλη:")
        self.lbl_fade_curve.grid(row=0, column=4, padx=(15, 5), pady=5)

        self.fade_curve_var = ctk.StringVar(
            value=self._curve_label_from_key(self.settings.get("fade_curve", "linear"))
        )
        
        def on_curve_change(choice):
            if choice in ["Νορμάλ", "Normal"]:
                val = "linear"
            elif choice in ["Ήπια", "Soft"]:
                val = "logarithmic"
            else:
                val = "exponential"
            self.settings["fade_curve"] = val
            save_settings(self.settings)

        # Δημιουργία του Dropdown Menu (Αυτό έλειπε)
        self.fade_curve_menu = ctk.CTkOptionMenu(
            grid_fade,
            values=["Νορμάλ", "Ήπια", "Επιθετική"],
            variable=self.fade_curve_var,
            width=160,
            command=on_curve_change
        )
        self.fade_curve_menu.grid(row=0, column=5, padx=5, pady=5)

        # --- Progress Bar ---
        self.progress_bar = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.stop()

        # --- Save / Skip Buttons ---
        self.save_row = ctk.CTkFrame(self, fg_color="transparent")
        self.save_row.pack(fill="x", padx=15, pady=10)

        self.btn_save = ctk.CTkButton(self.save_row, text="", fg_color="green", hover_color="darkgreen", height=45, font=("Arial", 15, "bold"), command=self.process_and_save)
        self.btn_save.pack(side="left", fill="x", expand=True)

        self.btn_skip = ctk.CTkButton(self.save_row, text="", width=110, height=45, fg_color="#9b7025", hover_color="#46341a", command=self.skip_current)

        # Dynamically apply language translations
        self.update_ui_texts()

        # Push initial state to history stack
        self._history.push(self._get_current_state())

        # --- Drag & Drop (μόνο αν είναι εγκατεστημένο το tkinterdnd2) ---
        if DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<DropEnter>>", self._on_dnd_enter)
                self.dnd_bind("<<DropLeave>>", self._on_dnd_leave)
                self.dnd_bind("<<Drop>>", self._on_dnd_drop)
            except Exception:
                pass

    def _parse_dnd_paths(self, data):
        """Μετατρέπει το raw string ενός <<Drop>> event σε λίστα από paths.
        Παίζει σωστά και με paths που περιέχουν κενά (τυλιγμένα σε {})."""
        import re
        raw = re.findall(r"\{[^{}]*\}|\S+", data)
        return [p.strip("{}") for p in raw]

    def _on_dnd_enter(self, event):
        # Οπτική ένδειξη ότι το παράθυρο δέχεται drop
        try:
            self.file_frame.configure(border_width=2, border_color="#2ecc71")
        except Exception:
            pass
        return event.action

    def _on_dnd_leave(self, event):
        try:
            self.file_frame.configure(border_width=0)
        except Exception:
            pass

    def _on_dnd_drop(self, event):
        try:
            self.file_frame.configure(border_width=0)
        except Exception:
            pass

        paths = self._parse_dnd_paths(event.data)
        if not paths:
            return

        path = paths[0]
        if os.path.isdir(path):
            self._load_folder_path(path)
        elif os.path.isfile(path) and path.lower().endswith(AUDIO_EXTENSIONS):
            self._load_single_file_path(path)
        else:
            messagebox.showwarning(_("warning"), _("no_audio_files"))

    def _curve_label_from_key(self, key):
        is_el = self.settings.get("language", "el") == "el"
        key_lower = str(key).lower()
        if "log" in key_lower or "ήπια" in key_lower or "soft" in key_lower:
            return "Ήπια" if is_el else "Soft"
        elif "exp" in key_lower or "επιθετική" in key_lower or "aggressive" in key_lower:
            return "Επιθετική" if is_el else "Aggressive"
        else:
            return "Νορμάλ" if is_el else "Normal"

    def update_ui_texts(self):
        # Window Title
        self.title(_("app_title"))

        # Buttons and controls
        self.btn_open.configure(text=_("open"))
        self.btn_open_folder.configure(text=_("open_folder"))
        self.btn_auto_silence.configure(text=_("auto_trim"))
        self.btn_auto_gain.configure(text=_("auto_gain"))
        self.btn_save.configure(text=_("save"))
        self.btn_skip.configure(text=_("skip"))

        # Section Headers
        self.lbl_trim_header.configure(text=_("trim_section_title"))
        self.lbl_start_trim.configure(text=_("start_trim_label"))
        self.lbl_end_trim.configure(text=_("end_trim_label"))

        self.lbl_vol_header.configure(text=_("volume_section_title"))
        self.lbl_volume.configure(text=_("volume_label", self.slider_volume.get()))
        self.btn_reset_vol.configure(text=_("reset_volume"))

        self.lbl_fade_header.configure(text=_("fade_section_title"))
        self.lbl_fade_in.configure(text=_("fade_in_label"))
        self.lbl_fade_out.configure(text=_("fade_out_label"))

        # Ενημέρωση Label & Επιλογών Καμπύλης Fade
        if hasattr(self, 'lbl_fade_curve'):
            is_el = self.settings.get("language", "el") == "el"
            self.lbl_fade_curve.configure(text="Καμπύλη:" if is_el else "Curve:")
            
            # Δυναμικές επιλογές ανά ανάλογα με τη γλώσσα
            curve_options = ["Νορμάλ", "Ήπια", "Επιθετική"] if is_el else ["Normal", "Soft", "Aggressive"]
            self.fade_curve_menu.configure(values=curve_options)
            
            # Συγχρονισμός της τρέχουσας τιμής
            current_curve = str(self.settings.get("fade_curve", "linear")).lower()
            if "log" in current_curve or "ήπια" in current_curve or "soft" in current_curve:
                self.fade_curve_var.set("Ήπια" if is_el else "Soft")
            elif "exp" in current_curve or "επιθετική" in current_curve or "aggressive" in current_curve:
                self.fade_curve_var.set("Επιθετική" if is_el else "Aggressive")
            else:
                self.fade_curve_var.set("Νορμάλ" if is_el else "Normal")

        # File and info details
        if self.file_path:
            filename = os.path.basename(self.file_path)
            self.lbl_file.configure(text=filename)
            self.lbl_info.configure(text=_("duration_format", self.duration_sec, self.file_path.split('.')[-1].upper()))
        else:
            self.lbl_file.configure(text=_("no_file"))
            self.lbl_info.configure(text=_("duration_unknown"))

        self._update_queue_ui()

    def set_controls_state(self, state):
        self.btn_open.configure(state=state)
        self.btn_open_folder.configure(state=state)
        self.btn_save.configure(state=state)
        self.btn_skip.configure(state=state)
        self.btn_settings.configure(state=state)
        self.btn_auto_silence.configure(state=state)
        self.btn_auto_gain.configure(state=state)

    def _add_context_menu(self, entry):
        """Δεξί-κλικ μενού (Αποκοπή/Αντιγραφή/Επικόλληση/Επιλογή Όλων) για CTkEntry.
        Δουλεύει σταθερά ανεξάρτητα από πληκτρολόγιο/layout, γιατί χειρίζεται
        απευθείας το πραγματικό tkinter.Entry που κρύβεται μέσα στο CTkEntry
        (self._entry), αντί να βασίζεται σε keyboard shortcuts."""
        real_entry = getattr(entry, "_entry", entry)

        menu = Menu(entry, tearoff=0)

        def do_copy():
            try:
                if real_entry.selection_present():
                    text = real_entry.selection_get()
                else:
                    text = real_entry.get()
                real_entry.clipboard_clear()
                real_entry.clipboard_append(text)
            except Exception:
                pass

        def do_cut():
            try:
                do_copy()
                if real_entry.selection_present():
                    real_entry.delete("sel.first", "sel.last")
            except Exception:
                pass

        def do_paste():
            try:
                text = real_entry.clipboard_get()
            except Exception:
                return
            try:
                if real_entry.selection_present():
                    real_entry.delete("sel.first", "sel.last")
                real_entry.insert("insert", text)
            except Exception:
                pass

        def do_select_all():
            try:
                real_entry.select_range(0, "end")
                real_entry.icursor("end")
            except Exception:
                pass

        menu.add_command(label=_("cut_menu"), command=do_cut)
        menu.add_command(label=_("copy_menu"), command=do_copy)
        menu.add_command(label=_("paste_menu"), command=do_paste)
        menu.add_separator()
        menu.add_command(label=_("select_all_menu"), command=do_select_all)

        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        entry.bind("<Button-3>", show_menu)

    def _show_tag_editor_dialog(self, guessed_tags):
        """Μικρό modal παράθυρο επιβεβαίωσης/διόρθωσης Τίτλου/Καλλιτέχνη/Άλμπουμ πριν την αποθήκευση.
        Επιστρέφει dict με τα τελικά tags, ή None αν ο χρήστης ακυρώσει."""
        result = {"value": None}

        win = ctk.CTkToplevel(self)
        win.title(_("song_info_title"))
        win.geometry("420x260")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        labels = {
            "title": _("title_label"),
            "artist": _("artist_label"),
            "album": _("album_label"),
        }

        frame = ctk.CTkFrame(win, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=15, pady=15)

        entries = {}
        for i, key in enumerate(["title", "artist", "album"]):
            ctk.CTkLabel(frame, text=labels[key], anchor="w", width=100).grid(row=i, column=0, sticky="w", pady=6)
            ent = ctk.CTkEntry(frame, width=250)
            ent.insert(0, guessed_tags.get(key, ""))
            ent.grid(row=i, column=1, pady=6, padx=(8, 0))
            self._add_context_menu(ent)
            entries[key] = ent

        def confirm():
            result["value"] = {k: e.get().strip() for k, e in entries.items()}
            win.destroy()

        def cancel():
            result["value"] = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", cancel)

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(btn_row, text=_("cancel"), fg_color="gray", command=cancel).pack(side="left")
        ctk.CTkButton(btn_row, text=_("dialog_save"), fg_color="green", hover_color="darkgreen", command=confirm).pack(side="right")

        self.wait_window(win)
        return result["value"]

    def open_settings_window(self):
        win = ctk.CTkToplevel(self)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        # --- Section 1: Single Song Settings ---
        frame_single = ctk.CTkFrame(win)
        frame_single.pack(fill="x", padx=15, pady=(15, 8))
        self.lbl_single_header = ctk.CTkLabel(frame_single, text="", font=("Arial", 14, "bold"))
        self.lbl_single_header.pack(anchor="w", padx=10, pady=(8, 2))

        row1 = ctk.CTkFrame(frame_single, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(0, 10))
        self.lbl_after_save = ctk.CTkLabel(row1, text="")
        self.lbl_after_save.pack(side="left")
        
        single_var = ctk.StringVar(value=_("clear_fields") if self.settings.get("single_after_save") == "reset" else _("keep_settings"))
        self.single_menu = ctk.CTkOptionMenu(row1, values=[_("clear_fields"), _("keep_settings")], variable=single_var, width=190)
        self.single_menu.pack(side="right")

        # --- Section 2: Batch Settings ---
        frame_batch = ctk.CTkFrame(win)
        frame_batch.pack(fill="x", padx=15, pady=8)
        self.lbl_batch_header = ctk.CTkLabel(frame_batch, text="", font=("Arial", 14, "bold"))
        self.lbl_batch_header.pack(anchor="w", padx=10, pady=(8, 2))

        row2 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=5)
        self.lbl_between_songs = ctk.CTkLabel(row2, text="")
        self.lbl_between_songs.pack(side="left")
        
        batch_var = ctk.StringVar(value=_("clear_fields") if self.settings.get("batch_after_save") == "reset" else _("keep_settings"))
        self.batch_menu = ctk.CTkOptionMenu(row2, values=[_("clear_fields"), _("keep_settings")], variable=batch_var, width=190)
        self.batch_menu.pack(side="right")

        self.lbl_output_folder = ctk.CTkLabel(frame_batch, text="")
        self.lbl_output_folder.pack(anchor="w", padx=10, pady=(8, 2))
        
        row3 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row3.pack(fill="x", padx=10, pady=(0, 5))
        dir_var = ctk.StringVar(value=self.settings.get("batch_output_dir", ""))
        self.ent_dir = ctk.CTkEntry(row3, textvariable=dir_var, placeholder_text="")
        self.ent_dir.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._add_context_menu(self.ent_dir)

        def browse_dir():
            chosen = filedialog.askdirectory()
            if chosen:
                dir_var.set(chosen)

        self.btn_browse = ctk.CTkButton(row3, text="", width=90, command=browse_dir)
        self.btn_browse.pack(side="right")

        row4 = ctk.CTkFrame(frame_batch, fg_color="transparent")
        row4.pack(fill="x", padx=10, pady=(5, 10))
        overwrite_var = ctk.BooleanVar(value=self.settings.get("batch_confirm_overwrite", True))
        self.chk_overwrite = ctk.CTkCheckBox(row4, text="", variable=overwrite_var)
        self.chk_overwrite.pack(anchor="w")

        # --- Section 3: Audio Export Options ---
        frame_export = ctk.CTkFrame(win)
        frame_export.pack(fill="x", padx=15, pady=8)
        self.lbl_export_header = ctk.CTkLabel(frame_export, text="", font=("Arial", 14, "bold"))
        self.lbl_export_header.pack(anchor="w", padx=10, pady=(8, 2))

        # Format Selector
        row_fmt = ctk.CTkFrame(frame_export, fg_color="transparent")
        row_fmt.pack(fill="x", padx=10, pady=4)
        self.lbl_fmt = ctk.CTkLabel(row_fmt, text="Format:")
        self.lbl_fmt.pack(side="left")
        fmt_var = ctk.StringVar(value=self.settings.get("export_format", "MP3"))
        
        def on_format_change(choice):
            if choice in ["WAV", "FLAC"]:
                bitrate_menu.configure(state="disabled")
            else:
                bitrate_menu.configure(state="normal")

        fmt_menu = ctk.CTkOptionMenu(row_fmt, values=["MP3", "WAV", "FLAC", "AAC", "OGG"], variable=fmt_var, width=190, command=on_format_change)
        fmt_menu.pack(side="right")

        # Bitrate Selector
        row_bit = ctk.CTkFrame(frame_export, fg_color="transparent")
        row_bit.pack(fill="x", padx=10, pady=4)
        self.lbl_bit = ctk.CTkLabel(row_bit, text="Bitrate:")
        self.lbl_bit.pack(side="left")
        bit_var = ctk.StringVar(value=self.settings.get("bitrate", "320k"))
        bitrate_menu = ctk.CTkOptionMenu(row_bit, values=["128k", "192k", "256k", "320k"], variable=bit_var, width=190)
        bitrate_menu.pack(side="right")
        if fmt_var.get() in ["WAV", "FLAC"]:
            bitrate_menu.configure(state="disabled")

        # Sample Rate Selector
        row_sr = ctk.CTkFrame(frame_export, fg_color="transparent")
        row_sr.pack(fill="x", padx=10, pady=(4, 10))
        self.lbl_sr = ctk.CTkLabel(row_sr, text="Sample Rate:")
        self.lbl_sr.pack(side="left")
        sr_var = ctk.StringVar(value=self.settings.get("sample_rate", "Original"))
        sr_menu = ctk.CTkOptionMenu(row_sr, values=["Original", "44.1 kHz", "48 kHz"], variable=sr_var, width=190)
        sr_menu.pack(side="right")

        # --- Section 4: General Settings ---
        frame_general = ctk.CTkFrame(win)
        frame_general.pack(fill="x", padx=15, pady=8)
        self.lbl_general_header = ctk.CTkLabel(frame_general, text="", font=("Arial", 14, "bold"))
        self.lbl_general_header.pack(anchor="w", padx=10, pady=(8, 2))

        row5 = ctk.CTkFrame(frame_general, fg_color="transparent")
        row5.pack(fill="x", padx=10, pady=(0, 10))
        self.lbl_language = ctk.CTkLabel(row5, text="")
        self.lbl_language.pack(side="left")
        
        lang_var = ctk.StringVar(value=_("greek_lang") if self.settings.get("language", "el") == "el" else _("english_lang"))
        
        def on_lang_change(selected_val):
            selected_lang = "el" if selected_val in ["Ελληνικά (EL)", "Greek (EL)"] else "en"
            set_language(selected_lang)
            self.settings["language"] = selected_lang
            save_settings(self.settings)
            
            self.update_ui_texts()
            update_settings_texts()

        self.lang_menu = ctk.CTkOptionMenu(row5, values=[_("greek_lang"), _("english_lang")], variable=lang_var, width=190, command=on_lang_change)
        self.lang_menu.pack(side="right")

        # Buttons
        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=15)

        def update_settings_texts():
            win.title(_("settings_title"))
            self.lbl_single_header.configure(text=_("single_song"))
            self.lbl_after_save.configure(text=_("after_save"))

            self.lbl_export_header.configure(
                text="Ρυθμίσεις Εξαγωγής" if self.settings.get("language", "el") == "el" else "Audio Export Settings"
            )

            self.single_menu.configure(values=[_("clear_fields"), _("keep_settings")])
            if single_var.get() in ["Καθαρισμός πεδίων", "Clear fields"]:
                single_var.set(_("clear_fields"))
            else:
                single_var.set(_("keep_settings"))

            self.lbl_batch_header.configure(text=_("batch_processing"))
            self.lbl_between_songs.configure(text=_("between_songs"))

            self.batch_menu.configure(values=[_("clear_fields"), _("keep_settings")])
            if batch_var.get() in ["Καθαρισμός πεδίων", "Clear fields"]:
                batch_var.set(_("clear_fields"))
            else:
                batch_var.set(_("keep_settings"))

            self.lbl_output_folder.configure(text=_("output_folder"))
            self.ent_dir.configure(placeholder_text=_("output_folder_placeholder"))
            self.btn_browse.configure(text=_("browse"))
            self.chk_overwrite.configure(text=_("confirm_overwrite"))

            self.lbl_general_header.configure(text=_("general_settings"))
            self.lbl_language.configure(text=_("language_label"))

            self.lang_menu.configure(values=[_("greek_lang"), _("english_lang")])
            if lang_var.get() in ["Ελληνικά (EL)", "Greek (EL)"]:
                lang_var.set(_("greek_lang"))
            else:
                lang_var.set(_("english_lang"))

            self.btn_restore.configure(text=_("restore_defaults"))
            self.btn_save_close.configure(text=_("save_close"))

        def restore_defaults():
            single_var.set(_("clear_fields"))
            batch_var.set(_("clear_fields"))
            dir_var.set("")
            overwrite_var.set(True)
            fmt_var.set("MP3")
            bit_var.set("320k")
            sr_var.set("Original")
            bitrate_menu.configure(state="normal")
            lang_var.set(_("greek_lang"))
            
            set_language("el")
            self.settings["language"] = "el"
            save_settings(self.settings)
            self.update_ui_texts()
            update_settings_texts()

        def save_and_close():
            self.settings["single_after_save"] = "reset" if single_var.get() in ["Καθαρισμός πεδίων", "Clear fields"] else "keep"
            self.settings["batch_after_save"] = "reset" if batch_var.get() in ["Καθαρισμός πεδίων", "Clear fields"] else "keep"
            self.settings["batch_output_dir"] = dir_var.get().strip()
            self.settings["batch_confirm_overwrite"] = overwrite_var.get()
            
            self.settings["export_format"] = fmt_var.get()
            self.settings["bitrate"] = bit_var.get()
            self.settings["sample_rate"] = sr_var.get()

            selected_lang = "el" if lang_var.get() in ["Ελληνικά (EL)", "Greek (EL)"] else "en"
            self.settings["language"] = selected_lang
            set_language(selected_lang)
            
            ok = save_settings(self.settings)
            self.update_ui_texts()
            win.destroy()

            if not ok:
                is_el_now = self.settings.get("language", "el") == "el"
                messagebox.showwarning(
                    "Προσοχή" if is_el_now else "Warning",
                    ("Οι ρυθμίσεις εφαρμόστηκαν για αυτή τη συνεδρία, αλλά δεν "
                     "ήταν δυνατή η αποθήκευσή τους στον δίσκο (πρόβλημα δικαιωμάτων "
                     f"εγγραφής στο:\n{SETTINGS_PATH}).")
                    if is_el_now else
                    ("Settings applied for this session, but could not be saved to "
                     f"disk (write permission issue at:\n{SETTINGS_PATH}).")
                )

        self.btn_restore = ctk.CTkButton(btn_row, text="", width=160, fg_color="#8F4427", hover_color="#612617", command=restore_defaults)
        self.btn_restore.pack(side="left")
        self.btn_save_close = ctk.CTkButton(btn_row, text="", fg_color="green", hover_color="darkgreen", command=save_and_close)
        self.btn_save_close.pack(side="right")

        update_settings_texts()
        win.geometry("460x650")

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[(_("audio_files_dialog"), "*.mp3 *.wav *.flac *.m4a *.aac *.ogg")])
        if not path:
            return
        self._load_single_file_path(path)

    def _load_single_file_path(self, path):
        """Φορτώνει ένα μεμονωμένο αρχείο ήχου (χρησιμοποιείται είτε από το κουμπί
        'Άνοιγμα' είτε από drag & drop)."""
        self.queue_files = []
        self.queue_index = -1
        self.batch_output_dir = None
        self._update_queue_ui()

        self._add_to_recent(path)
        self._start_load(path)

    def _add_to_recent(self, path):
        if not path or not os.path.exists(path):
            return
        recent = self.settings.get("recent_files", [])
        if not isinstance(recent, list):
            recent = []
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        max_recent = self.settings.get("max_recent_files", 8)
        self.settings["recent_files"] = recent[:max_recent]
        save_settings(self.settings)

    def load_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self._load_folder_path(folder)

    def _load_folder_path(self, folder):
        """Φορτώνει έναν φάκελο μαζικής επεξεργασίας (χρησιμοποιείται είτε από το
        κουμπί 'Άνοιγμα Φακέλου' είτε από drag & drop)."""
        files = sorted(
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(AUDIO_EXTENSIONS)
        )

        if not files:
            messagebox.showwarning(_("warning"), _("no_audio_files"))
            return

        output_dir = self.settings.get("batch_output_dir", "").strip()
        if not output_dir or not os.path.isdir(output_dir):
            chosen = filedialog.askdirectory(title=_("select_output_dir_batch"))
            if not chosen:
                return
            output_dir = chosen

        self.queue_files = files
        self.queue_index = 0
        self.batch_output_dir = output_dir

        self._add_to_recent(self.queue_files[self.queue_index])
        self._start_load(self.queue_files[self.queue_index])

    def _start_load(self, path, keep_settings=False):
        self._cancel_play_jobs()
        self.set_controls_state("disabled")
        self.progress_bar.start()
        self.lbl_file.configure(text=_("loading"))
        self._update_queue_ui()

        try:
            file_size_mb = os.path.getsize(path) / (1024 * 1024)
            if file_size_mb > 200:
                proceed = messagebox.askyesno(
                    _("large_file_title"),
                    _("large_file_msg", file_size_mb)
                )
                if not proceed:
                    self.progress_bar.stop()
                    self.set_controls_state("normal")
                    return
        except Exception:
            pass

        threading.Thread(
            target=self._load_file_worker,
            args=(path, keep_settings),
            daemon=True
        ).start()

    def _load_file_worker(self, path, keep_settings=False):
        try:
            audio = AudioSegment.from_file(path)
            duration_sec = len(audio) / 1000.0
            info = mediainfo(path)
            tags = info.get('TAG', {})
            self.after(0, lambda: self._on_file_loaded(path, audio, duration_sec, tags, keep_settings))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda: self._on_file_load_error(err_msg))

    def _on_file_loaded(self, path, audio, duration_sec, tags, keep_settings=False):
        self.file_path = path
        self.audio = audio
        self.duration_sec = duration_sec
        self.tags = tags

        filename = os.path.basename(path)
        self.lbl_file.configure(text=filename)
        self.lbl_info.configure(text=_("duration_format", self.duration_sec, path.split('.')[-1].upper()))
        self._update_queue_ui()

        if not keep_settings:
            self._reset_trim_controls()

        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _reset_trim_controls(self):
        self.ent_start_sec.delete(0, "end"); self.ent_start_sec.insert(0, "0")
        self.ent_start_dec.delete(0, "end"); self.ent_start_dec.insert(0, "00")
        self.ent_end_sec.delete(0, "end"); self.ent_end_sec.insert(0, "0")
        self.ent_end_dec.delete(0, "end"); self.ent_end_dec.insert(0, "00")
        self.slider_volume.set(0)
        self.update_vol_label(0)
        self.combo_fade_in.set("0.0")
        self.combo_fade_out.set("0.0")
        self._history.push(self._get_current_state())

    def _get_current_state(self):
        return {
            "start_sec": self.ent_start_sec.get(),
            "start_dec": self.ent_start_dec.get(),
            "end_sec": self.ent_end_sec.get(),
            "end_dec": self.ent_end_dec.get(),
            "volume": self.slider_volume.get(),
            "fade_in": self.combo_fade_in.get(),
            "fade_out": self.combo_fade_out.get(),
        }

    def _apply_state(self, state):
        self._updating_from_history = True
        self.ent_start_sec.delete(0, "end"); self.ent_start_sec.insert(0, state.get("start_sec", "0"))
        self.ent_start_dec.delete(0, "end"); self.ent_start_dec.insert(0, state.get("start_dec", "00"))
        self.ent_end_sec.delete(0, "end"); self.ent_end_sec.insert(0, state.get("end_sec", "0"))
        self.ent_end_dec.delete(0, "end"); self.ent_end_dec.insert(0, state.get("end_dec", "00"))
        self.slider_volume.set(state["volume"])
        self.update_vol_label(state["volume"])
        self.combo_fade_in.set(state["fade_in"])
        self.combo_fade_out.set(state["fade_out"])
        self._updating_from_history = False

    def _on_control_change(self, *args):
        if not self._updating_from_history:
            self._history.push(self._get_current_state())

    def undo(self):
        state = self._history.undo()
        if state:
            self._apply_state(state)

    def redo(self):
        state = self._history.redo()
        if state:
            self._apply_state(state)

    def _on_file_load_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.lbl_file.configure(text=_("no_file"))
        messagebox.showerror(_("error"), _("load_file_error", err_msg))

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
            messagebox.showinfo(_("completed"), _("batch_complete"))
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

        self.lbl_file.configure(text=_("no_file"))
        self.lbl_info.configure(text=_("duration_unknown"))
        self._update_queue_ui()

        if not keep_settings:
            self._reset_trim_controls()

    # ------------------------------------------------------------------
    #  Μία μόνο διεργασία — αν ήδη τρέχει η εφαρμογή, το εικονίδιο απλά τη φέρνει
    #  μπροστά αντί να ανοίγει δεύτερο παράθυρο (βλ. acquire_single_instance_lock).
    # ------------------------------------------------------------------
    def _start_single_instance_listener(self, sock):
        def accept_loop():
            while True:
                try:
                    conn, _addr = sock.accept()
                except OSError:
                    break
                try:
                    conn.settimeout(1.0)
                    conn.recv(16)
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                try:
                    self.after(0, self._bring_window_to_front)
                except Exception:
                    break
        threading.Thread(target=accept_loop, daemon=True).start()

    def _bring_window_to_front(self):
        try:
            if self.state() == "iconic":
                self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(250, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass

    def on_closing(self):
        self._cancel_play_jobs()
        for job_id in list(self._eq_anim_jobs.values()):
            self.after_cancel(job_id)
        try:
            temp_file = os.path.join(tempfile.gettempdir(), "trim_preview.wav")
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass
        try:
            save_settings(self.settings)
        except Exception:
            pass
        if self._single_instance_sock is not None:
            try:
                self._single_instance_sock.close()
            except Exception:
                pass
        self.destroy()

    def update_vol_label(self, val):
        self.lbl_volume.configure(text=_("volume_label", float(val)))

    def set_gain(self, gain_val):
        self.slider_volume.set(gain_val)
        self.update_vol_label(gain_val)
        self._on_control_change()

    def auto_detect_silence(self):
        if not self.audio:
            messagebox.showwarning(_("warning"), _("load_file_first"))
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
            err_msg = str(e)
            self.after(0, lambda: self._on_auto_silence_error(err_msg))

    def _parse_time_fields(self, ent_sec, ent_dec):
        sec = float(ent_sec.get().strip() or 0)
        dec_str = ent_dec.get().strip() or "0"
        dec = float(dec_str) / (10 ** len(dec_str)) if dec_str != "0" else 0.0
        return sec + dec

    def _on_auto_silence_done(self, start_ms, end_ms):
        start_val = start_ms / 1000.0
        end_val = end_ms / 1000.0
        
        self.ent_start_sec.delete(0, "end"); self.ent_start_sec.insert(0, str(int(start_val)))
        self.ent_start_dec.delete(0, "end"); self.ent_start_dec.insert(0, f"{int(round((start_val % 1) * 100)):02d}")

        self.ent_end_sec.delete(0, "end"); self.ent_end_sec.insert(0, str(int(end_val)))
        self.ent_end_dec.delete(0, "end"); self.ent_end_dec.insert(0, f"{int(round((end_val % 1) * 100)):02d}")

        self._on_control_change()
        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _on_auto_silence_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        messagebox.showerror(_("error"), _("auto_silence_error", err_msg))

    def auto_calculate_gain(self):
        if not self.audio:
            messagebox.showwarning(_("warning"), _("load_file_first"))
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
            err_msg = str(e)
            self.after(0, lambda: self._on_auto_gain_error(err_msg))

    def _on_auto_gain_done(self, gain):
        self.set_gain(gain)
        self.progress_bar.stop()
        self.set_controls_state("normal")

    def _on_auto_gain_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        messagebox.showerror(_("error"), _("auto_gain_error", err_msg))

    def _create_equalizer(self, parent, width=120, height=32, bars=10):
        canvas = Canvas(parent, width=width, height=height, bg="#1a1a1a", highlightthickness=0)
        canvas.eq_bars = []
        bar_width = 8
        gap = 3
        num_segments = 10
        seg_gap = 1
        total_width = bars * bar_width + (bars - 1) * gap
        start_x = (width - total_width) / 2
        seg_height = (height - (num_segments - 1) * seg_gap) / num_segments

        colors = [
            "#2ecc71", "#2ecc71", "#2ecc71", "#2ecc71",  # Green
            "#f1c40f", "#f1c40f",                       # Yellow
            "#e67e22", "#e67e22",                       # Orange
            "#e74c3c", "#e74c3c"                        # Red
        ]

        for i in range(bars):
            x0 = start_x + i * (bar_width + gap)
            x1 = x0 + bar_width
            bar_segments = []
            for j in range(num_segments):
                y1 = height - (j * (seg_height + seg_gap))
                y0 = y1 - seg_height
                rect = canvas.create_rectangle(x0, y0, x1, y1, fill="#2b2b2b", outline="")
                bar_segments.append((rect, colors[j]))
            canvas.eq_bars.append(bar_segments)
        return canvas

    def _draw_equalizer(self, canvas, heights):
        num_segments = len(canvas.eq_bars[0])
        max_h = int(canvas["height"])
        for bar_segments, bar_h in zip(canvas.eq_bars, heights):
            active_count = int((bar_h / max_h) * num_segments)
            for idx, (rect, active_color) in enumerate(bar_segments):
                if idx < active_count:
                    canvas.itemconfig(rect, fill=active_color)
                else:
                    canvas.itemconfig(rect, fill="#2b2b2b")

    def _reset_equalizer(self, canvas):
        for bar_segments in canvas.eq_bars:
            for rect, _ in bar_segments:
                canvas.itemconfig(rect, fill="#2b2b2b")

    # Σταθερό "σχήμα" για οπτική ποικιλομορφία ανάμεσα στις 10 μπάρες.
    # ΔΕΝ αντιπροσωπεύει πραγματικές συχνότητες (θα χρειαζόταν FFT/numpy) -
    # απλώς διαμορφώνει οπτικά την πραγματική ένταση (RMS/dBFS) του κάθε
    # χρονικού παραθύρου, ώστε οι μπάρες να "χορεύουν" με βάση αληθινά δεδομένα
    # ήχου και όχι τυχαίους αριθμούς.
    _EQ_BAR_SHAPE = [0.55, 0.7, 0.85, 1.0, 0.9, 0.95, 0.8, 0.65, 0.75, 0.6]

    def _compute_amplitude_profile(self, chunk, window_ms=120):
        """Υπολογίζει την πραγματική ένταση (dBFS) του chunk ανά μικρό χρονικό
        παράθυρο, κανονικοποιημένη σε 0.0-1.0. Αυτά τα δεδομένα οδηγούν το
        equalizer animation ώστε να αντανακλά το πραγματικό κομμάτι που παίζει."""
        profile = []
        total_len = len(chunk)
        pos = 0
        while pos < total_len:
            window = chunk[pos: pos + window_ms]
            try:
                level_db = window.dBFS
            except Exception:
                level_db = float("-inf")
            if level_db == float("-inf"):
                level_db = -60.0
            # -50dB (σχεδόν σιωπή) -> 0.0, 0dB (μέγιστο) -> 1.0
            normalized = max(0.0, min(1.0, (level_db + 50.0) / 50.0))
            profile.append(normalized)
            pos += window_ms
        return profile or [0.0]

    def _animate_equalizer(self, button):
        canvas = self.eq_canvases.get(button)
        if canvas is None:
            return
        try:
            # Ακύρωση τυχόν ήδη προγραμματισμένου loop για αυτό το κουμπί (αποφυγή διπλών loops)
            prev_job = self._eq_anim_jobs.get(button)
            if prev_job is not None:
                self.after_cancel(prev_job)

            h = int(canvas["height"])
            profile = self._eq_profiles.get(button)

            if profile:
                idx = self._eq_profile_idx.get(button, 0)
                level = profile[min(idx, len(profile) - 1)]
                self._eq_profile_idx[button] = idx + 1
                heights = [
                    int(max(h * 0.15, min(h - 4, level * shape * h)))
                    for shape in self._EQ_BAR_SHAPE
                ]
            else:
                # Fallback (δεν έχει υπολογιστεί profile) -> στατική χαμηλή ένδειξη, όχι τυχαία
                heights = [int(h * 0.2)] * 10

            self._draw_equalizer(canvas, heights)
            job_id = self.after(120, lambda: self._animate_equalizer(button))
            self._eq_anim_jobs[button] = job_id
        except Exception:
            pass

    def _stop_equalizer(self, button):
        job_id = self._eq_anim_jobs.pop(button, None)
        if job_id is not None:
            self.after_cancel(job_id)
        self._eq_profiles.pop(button, None)
        self._eq_profile_idx.pop(button, None)
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

                # Προϋπολογισμός πραγματικού προφίλ έντασης για το equalizer,
                # ώστε το animation να συγχρονίζεται με τον πραγματικό ήχο.
                # (Γίνεται ΜΕΤΑ το _set_playing_ui, που εσωτερικά καθαρίζει
                # τυχόν παλιό profile μέσω _stop_equalizer.)
                self._eq_profiles[button] = self._compute_amplitude_profile(chunk)
                self._eq_profile_idx[button] = 0

                duration_ms = int(len(chunk)) + 150
                job_id = self.after(duration_ms, lambda: self._reset_play_button(button))
                self._play_reset_jobs[button] = job_id

            winsound.PlaySound(temp_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            if button is not None:
                self._reset_play_button(button)
            messagebox.showerror(_("error"), _("preview_error", e))

    def _reset_play_button(self, button):
        self._set_playing_ui(button, False)
        self._play_reset_jobs.pop(button, None)

    def preview_start(self):
        if not self.audio:
            return
        try:
            start_sec = self._parse_time_fields(self.ent_start_sec, self.ent_start_dec)
            gain_db = float(self.slider_volume.get())
            fade_in_sec = float(self.combo_fade_in.get() or 0.0)

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
            end_sec = self._parse_time_fields(self.ent_end_sec, self.ent_end_dec)
            
            if end_sec >= self.duration_sec or end_sec < 0:
                return

            gain_db = float(self.slider_volume.get())
            fade_out_sec = float(self.combo_fade_out.get() or 0.0)

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
            messagebox.showwarning(_("warning"), _("load_file_first"))
            return

        try:
            trim_start_sec = self._parse_time_fields(self.ent_start_sec, self.ent_start_dec)
            trim_end_sec = self._parse_time_fields(self.ent_end_sec, self.ent_end_dec)
            gain_db = float(self.slider_volume.get())

            fade_in_sec = float(self.combo_fade_in.get() or 0.0)
            fade_out_sec = float(self.combo_fade_out.get() or 0.0)

            if trim_start_sec < 0 or trim_end_sec < 0:
                messagebox.showerror(_("error"), _("negative_trim"))
                return

            if trim_start_sec >= self.duration_sec:
                messagebox.showerror(_("error"), _("trim_start_large", trim_start_sec, self.duration_sec))
                return

            remaining = self.duration_sec - trim_start_sec
            if trim_end_sec >= remaining:
                messagebox.showerror(_("error"), _("trim_end_large", trim_end_sec, remaining))
                return

            if (trim_start_sec + trim_end_sec) >= self.duration_sec:
                messagebox.showerror(_("error"), _("trim_sum_large", trim_start_sec + trim_end_sec, self.duration_sec))
                return

            start_ms = int(trim_start_sec * 1000)
            end_ms = int((self.duration_sec - trim_end_sec) * 1000)

            if self._in_batch_mode():
                if not self.file_path or not self.batch_output_dir:
                    messagebox.showerror(_("error"), _("invalid_output_dir"))
                    return
                initial_file = os.path.basename(self.file_path)
                name_without_ext = os.path.splitext(initial_file)[0]
                target_ext = f".{self.settings.get('export_format', 'MP3').lower()}"
                out_path = os.path.join(self.batch_output_dir, name_without_ext + target_ext)

                if os.path.exists(out_path) and self.settings.get("batch_confirm_overwrite", True):
                    proceed = messagebox.askyesno(
                        _("file_exists"),
                        _("file_exists_msg", initial_file)
                    )
                    if not proceed:
                        return
            else:
                initial_dir = os.path.dirname(self.file_path) if self.file_path else None
                initial_file = os.path.basename(self.file_path) if self.file_path else ""
                ext = f".{self.settings.get('export_format', 'MP3').lower()}"

                out_path = filedialog.asksaveasfilename(
                    initialdir=initial_dir,
                    initialfile=initial_file,
                    defaultextension=ext,
                    filetypes=[
                        (_("all_audio_dialog"), "*.mp3 *.wav *.flac *.m4a *.aac *.ogg"),
                        (_("mp3_file_dialog"), "*.mp3"),
                        (_("wav_file_dialog"), "*.wav")
                    ]
                )
                if not out_path:
                    return

            # --- Καθαρισμός / Επιβεβαίωση Metadata (Τίτλος/Καλλιτέχνης/Άλμπουμ) ---
            guessed_tags = guess_clean_tags(self.tags, self.file_path)

            if self._in_batch_mode():
                # Στη μαζική επεξεργασία δεν διακόπτουμε με dialog ανά τραγούδι -
                # απλώς εφαρμόζουμε αυτόματα την "καθαρή" εκδοχή που μαντέψαμε.
                final_tags = guessed_tags
            else:
                final_tags = self._show_tag_editor_dialog(guessed_tags)
                if final_tags is None:
                    # Ο χρήστης πάτησε Ακύρωση στο παράθυρο metadata -> ματαίωση αποθήκευσης
                    return

            self.set_controls_state("disabled")
            self.progress_bar.start()
            self.btn_save.configure(text=_("saving"))

            fade_in_ms = int(fade_in_sec * 1000)
            fade_out_ms = int(fade_out_sec * 1000)

            export_format = self.settings.get("export_format", "MP3")
            bitrate = self.settings.get("bitrate", "320k")
            sample_rate = self.settings.get("sample_rate", "Original")
            fade_curve = self.settings.get("fade_curve", "linear")

            threading.Thread(
                target=self._export_worker,
                args=(
                    self.file_path, out_path, start_ms, end_ms, gain_db,
                    fade_in_ms, fade_out_ms, export_format, bitrate, sample_rate, fade_curve,
                    final_tags
                ),
                daemon=True
            ).start()

        except ValueError:
            messagebox.showerror(_("error"), _("invalid_numbers"))
        except Exception as e:
            messagebox.showerror(_("error"), str(e))

    def _export_worker(self, input_path, output_path, start_ms, end_ms, gain_db, fade_in_ms, fade_out_ms, export_format, bitrate, sample_rate, fade_curve, tags=None):
        """Worker thread για την επεξεργασία και εξαγωγή του ήχου."""
        tags = tags or {}
        # Κρατάμε μόνο τα μη-κενά πεδία, ώστε να μη γράφουμε άδεια metadata
        clean_tags = {k: v for k, v in tags.items() if v}

        try:
            in_ext = os.path.splitext(input_path)[1]
            out_ext = os.path.splitext(output_path)[1]
            total_ms = int(self.duration_sec * 1000)

            # 1. Έλεγχος για Smart Lossless Trim (Direct FFmpeg Stream Copy)
            if is_lossless_copy_eligible(start_ms, end_ms, total_ms, gain_db, fade_in_ms, fade_out_ms, in_ext, out_ext, sample_rate):
                start_sec = start_ms / 1000.0
                duration_sec = (end_ms - start_ms) / 1000.0
            
                cmd = [
                    AudioSegment.converter, "-y",
                    "-ss", str(start_sec),
                    "-i", input_path,
                    "-t", str(duration_sec),
                    "-c", "copy",
                ]
                # Ρητή εγγραφή των (πιθανώς διορθωμένων από τον χρήστη) tags,
                # ώστε να μην εξαρτόμαστε σιωπηλά στα defaults του ffmpeg.
                for key, value in clean_tags.items():
                    cmd += ["-metadata", f"{key}={value}"]
                cmd.append(output_path)
            
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, check=True)
                self.after(0, self._on_export_success)
                return

            # 2. Re-encoding Path (PyDub + New Audio Core)
            audio = AudioSegment.from_file(input_path)
            trimmed = audio[start_ms:end_ms]

            if gain_db != 0:
                trimmed = apply_safe_gain(trimmed, gain_db)

            if fade_in_ms > 0:
                trimmed = apply_custom_fade(trimmed, fade_in_ms, mode="in", curve_type=fade_curve)
            
            if fade_out_ms > 0:
                trimmed = apply_custom_fade(trimmed, fade_out_ms, mode="out", curve_type=fade_curve)

            if sample_rate and sample_rate != "Original":
                clean_sr = 44100 if "44.1" in str(sample_rate) else (48000 if "48" in str(sample_rate) else 44100)
                trimmed = trimmed.set_frame_rate(clean_sr)

            export_kwargs = {"format": export_format.lower()}

            # Το ffmpeg δεν αναγνωρίζει "aac" σαν muxer εξόδου, μόνο "adts"
            if export_format.upper() == "AAC":
                export_kwargs["format"] = "adts"

            if export_format.upper() in ["MP3", "AAC", "OGG", "M4A"]:
                export_kwargs["bitrate"] = bitrate

            if clean_tags:
                export_kwargs["tags"] = clean_tags

            trimmed.export(output_path, **export_kwargs)
            self.after(0, self._on_export_success)

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda: self._on_export_error(err_msg))

    def _on_export_success(self):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.btn_save.configure(text=_("save"))

        if self._in_batch_mode():
            self._advance_queue()
        else:
            messagebox.showinfo(_("success"), _("file_saved"))
            keep_settings = self.settings.get("single_after_save", "reset") == "keep"
            self.reset_state(keep_settings=keep_settings)

    def _on_export_error(self, err_msg):
        self.progress_bar.stop()
        self.set_controls_state("normal")
        self.btn_save.configure(text=_("save"))
        messagebox.showerror(_("error"), _("export_error", err_msg))

if __name__ == "__main__":
    is_primary, lock_sock = acquire_single_instance_lock()
    if not is_primary:
        # Υπάρχει ήδη ανοιχτή η εφαρμογή -- της στείλαμε σήμα να έρθει μπροστά,
        # τερματίζουμε αμέσως χωρίς να ανοίξουμε δεύτερο παράθυρο.
        sys.exit(0)
    app = AudioTrimmerApp(single_instance_sock=lock_sock)
    app.mainloop()