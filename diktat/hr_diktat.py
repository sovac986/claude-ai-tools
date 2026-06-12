# -*- coding: utf-8 -*-
"""
hr_diktat.py - Lokalna hrvatska diktacija za VS Code / Claude Code (Windows)
============================================================================
Pozadinski servis (push-to-talk):
    - drzi F13 (G1): snimanje
    - pusti F13: transkripcija -> paste u fokusirani prozor -> Enter
    - dupli tap F13: LOCK mod (snima bez drzanja), jos jedan tap = kraj
    - Esc tijekom snimanja ili transkripcije: odbaci (nista se ne lijepi)
    - tray ikona: sivo = mirno, crveno = snima, zuto = transkribira

Pokretanje:
    python hr_diktat.py                 (konzolno, za debug)
    pythonw hr_diktat.py                (headless servis; vidi start_diktat.ps1)
    python hr_diktat.py --detect        (detekcija imena tipke)
    python hr_diktat.py --list-devices  (lista audio uredjaja)

Log: diktat.log u ovom folderu.

Ovisnosti:
    pip install faster-whisper sounddevice numpy keyboard pyperclip pystray pillow
    pip install nvidia-cublas-cu12 nvidia-cudnn-cu12   (GPU)
"""

import os
import re
import sys
import time
import ctypes
import threading
from datetime import datetime

# ===========================================================================
# KONFIGURACIJA - jedino sto trebas mijenjati
# ===========================================================================
HOTKEY     = "f13"      # push-to-talk tipka (G1 mapirana na F13)
MODEL_SIZE = "large-v3-turbo"  # GPU float16 (RTX 5080); CPU int8 fallback
LANGUAGE   = "hr"
MIC_NAME   = "Arctis"   # substring imena input uredjaja; None = sistemski default
AUTO_PASTE = True       # False = samo kopira u clipboard
AUTO_ENTER = True       # nakon pastea posalji Enter (auto-submit poruke)
MIN_REC_S  = 0.3        # snimke krace od ovoga se odbacuju (slucajni tap)
EXIT_WITH_VSCODE = True # ugasi servis kad se VS Code (Code.exe) zatvori
TRAY       = True       # ikona u system trayu (sivo=mirno, crveno=snima, zuto=transkribira)

TAP_MAX_S    = 0.35     # pritisak kraci od ovoga = tap (ne push-to-talk)
DOUBLE_TAP_S = 0.40     # drugi pritisak unutar ovoga nakon tapa = lock mod

# Whisperu se daje kao kontekst da bolje pogadja tehnicke termine u diktatu
INITIAL_PROMPT = (
    "Diktat za programiranje na hrvatskom: Claude Code, CLAUDE.md, VS Code, "
    "Git, commit, push, skill, hook, watchdog, push-to-talk, Whisper, "
    "transkripcija, GPU, CPU, Python, PowerShell, Docker, Ollama, "
    "fine-tuning, model, large-v3, clipboard, terminal, chat."
)

# Tvrdoglave promasaje ispravi nakon transkripcije (case-insensitive)
REPLACEMENTS = {
    "kloot kod":  "Claude Code",
    "kloot kodu": "Claude Codeu",
    "klod kod":   "Claude Code",
    "klaud kod":  "Claude Code",
    "cloud kod":  "Claude Code",
    "cloud code": "Claude Code",
    "cloud.md":   "CLAUDE.md",
    "klod.md":    "CLAUDE.md",
    "claude.md":  "CLAUDE.md",
    "watchtok":   "watchdog",
    "vocok":      "watchdog",
    "ve es kod":  "VS Code",
    "vs kod":     "VS Code",
}
# ===========================================================================

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
LOG_FILE    = os.path.join(BASE_DIR, "diktat.log")
LOG_MAX_MB  = 10   # kad log naraste iznad ovoga, stari se arhivira u diktat.log.1

def _rotate_log():
    try:
        if os.path.getsize(LOG_FILE) > LOG_MAX_MB * 1024 * 1024:
            backup = LOG_FILE + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(LOG_FILE, backup)
    except Exception:
        pass

def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        _rotate_log()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            sys.stdout.buffer.write((line + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            try:
                print(line)
            except Exception:
                pass

# --- Singleton guard (SessionStart hook moze opaliti iz vise sesija) ----------
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "hr_diktat_singleton")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    log("Vec radi druga instanca, izlazim.")
    sys.exit(0)

# --- NVIDIA DLL-ovi iz pip wheelova (cublas/cudnn) za GPU mod -------------------
def _add_nvidia_dlls():
    import sysconfig
    sp = sysconfig.get_paths()["purelib"]
    for sub in (r"nvidia\cublas\bin", r"nvidia\cudnn\bin"):
        p = os.path.join(sp, sub)
        if os.path.isdir(p):
            os.add_dll_directory(p)
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

_add_nvidia_dlls()

import numpy as np
import sounddevice as sd
import keyboard
import pyperclip

SAMPLE_RATE = 16000

# --- CLI: --list-devices -------------------------------------------------------
if "--list-devices" in sys.argv:
    print(sd.query_devices())
    sys.exit(0)

# --- CLI: --detect (detekcija imena tipke) --------------------------------------
def detect_key(prompt="Pritisni tipku koju zelis koristiti (G1)..."):
    print(f"\n{prompt}")
    print("(Ctrl+C za odustajanje)\n")
    detected = threading.Event()
    result = {}

    def on_press(e):
        if e.name in ("ctrl", "alt", "shift", "windows", "caps lock"):
            return
        result["name"] = e.name
        detected.set()

    hook = keyboard.on_press(on_press)
    detected.wait(timeout=30)
    keyboard.unhook(hook)

    if "name" not in result:
        print("[!] Nije detektirana tipka, izlazim.")
        sys.exit(1)

    print(f"[OK] Detektirana tipka: \"{result['name']}\"")
    print(f"     Kopiraj ovo u HOTKEY = \"{result['name']}\" u skripti.\n")
    return result["name"]

if "--detect" in sys.argv:
    detect_key()
    sys.exit(0)

# --- Izbor mikrofona po imenu ----------------------------------------------------
def pick_device(name):
    if not name:
        return None
    candidates = []
    hostapis = sd.query_hostapis()
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            api = hostapis[dev["hostapi"]]["name"]
            candidates.append((idx, dev["name"], api))
    if not candidates:
        log(f"[!] Nijedan input uredjaj ne sadrzi \"{name}\" - koristim sistemski default.")
        return None
    # WASAPI tipicno NE podrzava 16 kHz direktno, pa uzmi prvi uredjaj
    # koji prolazi check za nas sample rate (MME/DirectSound resamplaju).
    for idx, dname, api in candidates:
        try:
            sd.check_input_settings(
                device=idx, samplerate=SAMPLE_RATE, channels=1, dtype="float32"
            )
        except Exception:
            continue
        log(f"[OK] Mikrofon #{idx}: {dname} ({api})")
        return idx
    log(f"[!] Nijedan \"{name}\" uredjaj ne podrzava {SAMPLE_RATE} Hz - koristim sistemski default.")
    return None

AUDIO_DEVICE = pick_device(MIC_NAME)

# --- Ucitavanje modela ----------------------------------------------------------
def load_model():
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel(MODEL_SIZE, device="cuda", compute_type="float16")
        log(f"[OK] Model {MODEL_SIZE} na GPU (float16)")
        return m
    except Exception as e:
        log(f"[!] GPU nije uspio ({e.__class__.__name__}: {e}), koristim CPU...")
        m = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        log(f"[OK] Model {MODEL_SIZE} na CPU (int8)")
        return m

log("Ucitavam Whisper model...")
model = load_model()

# Warmup: prva CUDA transkripcija zna trajati i 15+ s (JIT/autotune),
# pa to odradimo odmah da prvi pravi diktat bude brz.
log("[i] Warmup transkripcija...")
_t0 = time.time()
list(model.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"), language=LANGUAGE)[0])
log(f"[i] Warmup gotov za {time.time() - _t0:.1f}s")

# --- Snimanje ---------------------------------------------------------------------
recording    = False
transcribing = False
locked       = False    # double-tap lock mod: snima bez drzanja tipke
rec_start    = 0.0
last_tap     = 0.0      # vrijeme zadnjeg kratkog tapa (za double-tap)
stop_time    = 0.0      # vrijeme zadnjeg stop-a (cooldown protiv auto-repeata)
ignore_release = False
chunks       = []
lock         = threading.Lock()
cancel_event = threading.Event()

# --- Tray ikona -----------------------------------------------------------------
_tray = None

def _tray_image(color):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=color)
    return img

_TRAY_STATES = {
    "idle": ((110, 110, 110, 255), "HR diktat - mirno (drzi F13)"),
    "rec":  ((220, 40, 40, 255),   "HR diktat - SNIMA"),
    "busy": ((240, 180, 40, 255),  "HR diktat - transkribira..."),
}

def set_state(state):
    if _tray is not None:
        color, title = _TRAY_STATES[state]
        try:
            _tray.icon = _tray_image(color)
            _tray.title = title
        except Exception:
            pass

def _tray_thread():
    global _tray
    try:
        import pystray
        _tray = pystray.Icon(
            "hr_diktat", _tray_image(_TRAY_STATES["idle"][0]),
            _TRAY_STATES["idle"][1],
            menu=pystray.Menu(pystray.MenuItem("Izlaz", lambda: os._exit(0))),
        )
        _tray.run()
    except Exception:
        log("[!] Tray ikona nije uspjela, nastavljam bez nje.")

if TRAY:
    threading.Thread(target=_tray_thread, daemon=True).start()

def fix_terms(text):
    for wrong, right in REPLACEMENTS.items():
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
    return text

def audio_callback(indata, frames, t, status):
    if recording:
        with lock:
            chunks.append(indata.copy())

stream = sd.InputStream(
    samplerate=SAMPLE_RATE, channels=1, dtype="float32",
    callback=audio_callback, blocksize=1024, device=AUDIO_DEVICE,
)
stream.start()

def beep(freq=880, dur=120):
    try:
        import winsound
        winsound.Beep(freq, dur)
    except Exception:
        pass

# --- Transkripcija i paste ---------------------------------------------------------
def transcribe_and_paste():
    global transcribing
    with lock:
        if not chunks:
            log("[!] Nema audija.")
            return
        audio = np.concatenate(chunks).flatten()
        chunks.clear()

    dur = len(audio) / SAMPLE_RATE
    if dur < MIN_REC_S:
        log(f"[i] Snimka {dur:.2f}s prekratka, odbacujem.")
        return

    log(f"[...] Transkribiram {dur:.1f}s audija... (Esc = odbaci)")
    t0 = time.time()
    transcribing = True
    set_state("busy")
    cancel_event.clear()
    try:
        segments, _ = model.transcribe(
            audio,
            language=LANGUAGE,
            beam_size=5,
            initial_prompt=INITIAL_PROMPT,
            vad_filter=True,
            vad_parameters=dict(threshold=0.2, min_silence_duration_ms=500),
        )
        text = fix_terms(" ".join(s.text.strip() for s in segments).strip())
        elapsed = time.time() - t0

        if cancel_event.is_set():
            log(f"[i] Rezultat odbacen (Esc): {text}")
            return

        log(f"[OK] {elapsed:.1f}s -> {text}")

        if not text:
            log("[!] Nista prepoznato.")
            return

        pyperclip.copy(text)
        log("[i] Kopirano u clipboard.")
        if AUTO_PASTE:
            # pricekaj da modifier tipke budu pustene
            for _ in range(20):
                if not any(keyboard.is_pressed(k) for k in ("ctrl", "alt", "shift")):
                    break
                time.sleep(0.05)
            time.sleep(0.1)
            keyboard.send("ctrl+v")
            log("[i] Poslan Ctrl+V.")
            if AUTO_ENTER:
                time.sleep(0.15)
                keyboard.send("enter")
                log("[i] Poslan Enter.")
        else:
            log("[i] Tekst je u clipboardu, zalijepi s Ctrl+V.")
    except Exception:
        import traceback
        log("[!] GRESKA u transkripciji/pasteu:\n" + traceback.format_exc())
    finally:
        transcribing = False
        set_state("idle")

# --- Push-to-talk + double-tap lock logika --------------------------------------
def _stop_and_transcribe():
    global recording, locked, stop_time
    recording = False
    locked = False
    stop_time = time.time()
    beep(440)
    threading.Thread(target=transcribe_and_paste, daemon=True).start()

def _on_press(e):
    global recording, rec_start, locked, ignore_release
    if recording:
        if locked:
            # tap u lock modu = kraj snimanja
            ignore_release = True
            _stop_and_transcribe()
        return  # inace: Windows auto-repeat dok drzis tipku
    if time.time() - stop_time < 0.5:
        return  # cooldown: auto-repeat odmah nakon lock-stopa ne smije restartati
    if time.time() - last_tap < DOUBLE_TAP_S:
        locked = True
        log("[REC] LOCK mod: snimam bez drzanja, tapni jos jednom za kraj.")
        beep(1320, 80)
    else:
        log(f"[REC] Snimam... (pusti [{HOTKEY}] za stop, dupli tap = lock)")
    with lock:
        chunks.clear()
    rec_start = time.time()
    recording = True
    set_state("rec")
    beep(880)

def _on_release(e):
    global recording, last_tap, ignore_release
    if ignore_release:
        ignore_release = False
        return
    if not recording or locked:
        return  # u lock modu release ne zaustavlja snimanje
    if time.time() - rec_start < TAP_MAX_S:
        # kratki tap: mozda prvi dio double-tapa - tiho odbaci i zapamti vrijeme
        recording = False
        last_tap = time.time()
        with lock:
            chunks.clear()
        set_state("idle")
        return
    _stop_and_transcribe()

def _on_cancel(e):
    global recording, locked
    if recording:
        recording = False
        locked = False
        with lock:
            chunks.clear()
        beep(220, 250)
        set_state("idle")
        log("[i] Snimka odbacena (Esc).")
    elif transcribing:
        cancel_event.set()
        beep(220, 250)

keyboard.on_press_key(HOTKEY, _on_press, suppress=True)
keyboard.on_release_key(HOTKEY, _on_release, suppress=True)
keyboard.on_press_key("esc", _on_cancel)  # bez suppress - Esc i dalje radi normalno

# --- VS Code watchdog: ugasi se kad Code.exe nestane ---------------------------
def _vscode_watchdog():
    import subprocess
    while True:
        time.sleep(30)
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Code.exe", "/NH"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
        except Exception:
            continue
        if "Code.exe" not in out:
            log("[i] VS Code zatvoren - gasim servis.")
            os._exit(0)

if EXIT_WITH_VSCODE:
    threading.Thread(target=_vscode_watchdog, daemon=True).start()

log("=" * 50)
log(f"Spremno! Push-to-talk: drzi [{HOTKEY.upper()}], pusti za transkripciju.")
log("=" * 50)

try:
    keyboard.wait()
except KeyboardInterrupt:
    log("[i] Izlazim...")
finally:
    stream.stop()
    stream.close()
