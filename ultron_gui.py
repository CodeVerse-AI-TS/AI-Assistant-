import os
import re
import json
import asyncio
import tempfile
import uuid
import subprocess
import webbrowser
import datetime
import threading
import time
import urllib.parse
import sqlite3
import ctypes
from pathlib import Path
import pyautogui
import os

import sounddevice as sd
import numpy as np
import soundfile as sf
import edge_tts
from playsound import playsound
import customtkinter as ctk
import tkinter as tk
from dotenv import load_dotenv
from google import genai
import pystray
from PIL import Image, ImageDraw, ImageGrab

# ================= SETUP =================

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("ERROR: No GEMINI_API_KEY found. Looked at:", env_path)
    exit()

client = genai.Client(api_key=api_key)

# ================= SPEECH ENGINE =================
# faster-whisper replaces Vosk for both wake-word and command recognition.
# Install once with: pip install faster-whisper
from faster_whisper import WhisperModel

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
WHISPER_SAMPLE_RATE = 16000
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "3"))

print(f"Loading speech model: {WHISPER_MODEL} ...")

def _load_whisper_model():
    # CPU is the safe default. Use WHISPER_DEVICE=cuda only after
    # CUDA/cuDNN are installed and working on this PC.
    requested = os.getenv("WHISPER_DEVICE", "cpu").lower()

    if requested == "cuda":
        try:
            return WhisperModel(
                WHISPER_MODEL,
                device="cuda",
                compute_type="float16",
            )
        except Exception as exc:
            print("CUDA speech backend unavailable; falling back to CPU INT8:", exc)

    return WhisperModel(
        WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
    )

whisper_model = _load_whisper_model()

WAKE_WORD = "ultron"
WAKE_ALIASES = {
    "ultron", "altron", "ultrun", "ultrone", "ultra", "eltron", "ultrons"
}

# ================= PERSISTENT MEMORY =================
# Memory is stored locally beside this script in ultron_memory.db.
# Only explicit "remember ..." requests are saved automatically.

MEMORY_DB = Path(__file__).resolve().parent / "ultron_memory.db"

def _memory_connection():
    conn = sqlite3.connect(MEMORY_DB, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def remember(memory_text):
    memory_text = memory_text.strip()
    if not memory_text:
        return False
    conn = _memory_connection()
    try:
        conn.execute(
            "INSERT INTO memories (memory, created_at) VALUES (?, ?)",
            (memory_text, datetime.datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        return True
    finally:
        conn.close()

def get_memories(query=None, limit=12):
    conn = _memory_connection()
    try:
        if query:
            tokens = [t for t in re.findall(r"[a-zA-Z0-9']+", query.lower()) if len(t) > 2]
            if tokens:
                clauses = " OR ".join("LOWER(memory) LIKE ?" for _ in tokens)
                params = [f"%{t}%" for t in tokens] + [limit]
                rows = conn.execute(
                    f"SELECT id, memory FROM memories WHERE {clauses} "
                    "ORDER BY id DESC LIMIT ?",
                    params
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, memory FROM memories ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, memory FROM memories ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return rows
    finally:
        conn.close()

def forget_memory(memory_id=None, search_text=None):
    conn = _memory_connection()
    try:
        if memory_id is not None:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        elif search_text:
            cur = conn.execute(
                "DELETE FROM memories WHERE LOWER(memory) LIKE ?",
                (f"%{search_text.lower().strip()}%",)
            )
        else:
            cur = conn.execute("DELETE FROM memories")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()

def memory_count():
    conn = _memory_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()

# Persistent voice settings are stored in the same local SQLite database.
def _settings_connection():
    conn = sqlite3.connect(MEMORY_DB, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def get_setting(key, default=None):
    conn = _settings_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()

def save_setting(key, value):
    conn = _settings_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()

def memory_context(user_text):
    rows = get_memories(user_text, limit=8)
    if not rows:
        rows = get_memories(limit=8)
    if not rows:
        return ""
    return "\n".join(f"- {memory}" for _, memory in rows)

_memory_connection().close()

conversation_history = []
CONVERSATION_LIMIT = 10

system_prompt = (
    "You are Ultron, a helpful AI assistant. "
    "Keep responses short, clear, and conversational, "
    "since they may be spoken out loud. "
    "Use remembered user preferences and facts when relevant. "
    "Do not claim to remember something that is not in the memory context."
)

# Curated set of natural-sounding neural voices (deep/commanding options first)
VOICE_OPTIONS = {
    "Guy (US, deep)": "en-US-GuyNeural",
    "Ryan (UK, deep)": "en-GB-RyanNeural",
    "Christopher (US)": "en-US-ChristopherNeural",
    "Eric (US)": "en-US-EricNeural",
    "Andrew (US)": "en-US-AndrewNeural",
    "Thomas (UK)": "en-GB-ThomasNeural",
}
saved_voice = get_setting("voice", "en-GB-RyanNeural")
saved_rate = get_setting("rate", "+10%")
saved_pitch = get_setting("pitch", "-50Hz")

# Voice settings now survive restarts.
current_voice = saved_voice if saved_voice in VOICE_OPTIONS.values() else "en-GB-RyanNeural"
current_rate = saved_rate if re.fullmatch(r"[+-]\d+%", saved_rate) else "+10%"
current_pitch = saved_pitch if re.fullmatch(r"[+-]\d+Hz", saved_pitch) else "-50Hz"
robotic_filter_on = False

# Phonetic fragments to catch even when Vosk mishears "Ultron"
WAKE_FRAGMENTS = ["ultr", "eltr", "altr", "hultr"]

# Shared flag so the wake-word loop pauses while a message is being handled
is_busy = False
pending_confirmation = None

# Apps Ultron knows how to open. Add more here: "keyword": "executable or command"
COMMON_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "start ms-settings:",
    "chrome": "start chrome",
    "edge": "start msedge",
    "word": "start winword",
    "excel": "start excel",
    "spotify": "start spotify",
}

# Windows process names used by the close-app command.
APP_PROCESSES = {
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "Taskmgr.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "spotify": "Spotify.exe",
}

# ================= COLORS =================

BG = "#0a0a14"
PANEL = "#12121f"
PANEL_2 = "#191929"
LINE = "#2a2a44"
VIOLET = "#9d7cff"
VIOLET_DIM = "#4a3a7a"
CYAN = "#5eead4"
CYAN_DIM = "#2a5f5a"
TEXT = "#e8e6fb"
MUTED = "#6f6f90"

# Kept as aliases so nothing else in the file needs renaming
EMBER = VIOLET
EMBER_DIM = VIOLET_DIM


# ================= CORE FUNCTIONS =================

def _record_audio(duration):
    audio = sd.rec(
        int(duration * WHISPER_SAMPLE_RATE),
        samplerate=WHISPER_SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def _transcribe_audio(audio, *, wake=False):
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak < 0.008:
        return ""

    if peak > 1.0:
        audio = audio / peak

    segments, _info = whisper_model.transcribe(
        audio,
        language="en",
        beam_size=1 if wake else WHISPER_BEAM_SIZE,
        best_of=1,
        temperature=0.0,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 350,
            "speech_pad_ms": 120,
        },
        condition_on_previous_text=False,
        without_timestamps=True,
        initial_prompt="Ultron." if wake else None,
        hotwords="Ultron" if wake else None,
    )

    return " ".join(seg.text.strip() for seg in segments).strip()


def _normalize_transcript(text):
    text = re.sub(r"[^a-zA-Z0-9' ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _wake_word_detected(text):
    import difflib
    normalized = _normalize_transcript(text)
    if not normalized:
        return False
    for word in normalized.split():
        if word in WAKE_ALIASES:
            return True
        if difflib.SequenceMatcher(None, word, WAKE_WORD).ratio() >= 0.82:
            return True
    return bool(re.search(r"ultr[oou]n?", normalized))


def _remove_wake_word(text):
    return re.sub(
        r"\b(?:ultron|altron|ultrun|ultrone|ultra|eltron|ultrons)\b[,.!?;:]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _record_until_silence(max_seconds=9.0, start_timeout=3.0, silence_seconds=0.85):
    """Capture speech naturally: start on voice, stop after a short silence."""
    samplerate = WHISPER_SAMPLE_RATE
    blocksize = 1600  # 100 ms
    chunks = []
    started = False
    silent_for = 0.0
    waited = 0.0
    threshold = 0.015

    with sd.InputStream(
        samplerate=samplerate,
        channels=1,
        dtype="float32",
        blocksize=blocksize,
    ) as stream:
        total = 0.0
        while total < max_seconds:
            chunk, _overflowed = stream.read(blocksize)
            audio = np.asarray(chunk, dtype=np.float32).reshape(-1)
            rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
            chunks.append(audio.copy())
            step = blocksize / samplerate
            total += step

            if not started:
                waited += step
                if rms >= threshold:
                    started = True
                    silent_for = 0.0
                elif waited >= start_timeout:
                    break
                continue

            if rms < threshold:
                silent_for += step
                if silent_for >= silence_seconds:
                    break
            else:
                silent_for = 0.0

    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def listen(duration=7):
    audio = _record_until_silence(max_seconds=float(duration))
    return _transcribe_audio(audio, wake=False)


def listen_after_wake():
    audio = _record_until_silence(max_seconds=10.0, start_timeout=4.0, silence_seconds=0.9)
    return _transcribe_audio(audio, wake=False)


def _clean_target(target):
    return re.sub(r"\s+", " ", target.lower().strip(" .!?"))


def take_screenshot():
    screenshots_dir = Path.home() / "Pictures" / "Ultron Screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = screenshots_dir / f"ultron_{timestamp}.png"
    try:
        image = ImageGrab.grab(all_screens=True)
    except TypeError:
        image = ImageGrab.grab()
    image.save(path)
    return path


def close_application(target):
    target = _clean_target(target)

    # Exact known app names first.
    if target in APP_PROCESSES:
        process = APP_PROCESSES[target]
    else:
        # Allow a few natural variants without accepting arbitrary commands.
        aliases = {
            "file explorer": "explorer",
            "the explorer": "explorer",
            "google chrome": "chrome",
            "microsoft edge": "edge",
            "vs code": "code",
            "visual studio code": "code",
        }
        normalized = aliases.get(target, target)
        if normalized == "code":
            process = "Code.exe"
        else:
            return None

    result = subprocess.run(
        ["taskkill", "/IM", process, "/T"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode == 0:
        return f"Closing {target}."
    return f"I couldn't find {target} running."


def system_tasklist(limit=12):
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        return "I couldn't read the running applications."

    names = []
    for line in result.stdout.splitlines():
        match = re.match(r'"([^"]+)",', line)
        if match:
            name = match.group(1)
            if name not in names:
                names.append(name)
        if len(names) >= limit:
            break
    return "Some running processes are: " + ", ".join(names) + "."


def _press_vk(vk, presses=1):
    """Send a Windows virtual-key event without another automation package."""
    if os.name != "nt":
        return False
    KEYEVENTF_KEYUP = 0x0002
    for _ in range(max(1, presses)):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return True


def volume_up(): return _press_vk(0xAF, 3)
def volume_down(): return _press_vk(0xAE, 3)
def volume_mute(): return _press_vk(0xAD, 1)
def media_key(vk): return _press_vk(vk, 1)

def show_desktop():
    if os.name != "nt": return False
    VK_LWIN, VK_D = 0x5B, 0x44
    ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_D, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_D, 0, 0x0002, 0)
    ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0x0002, 0)
    return True


def lock_pc():
    return os.name == "nt" and bool(ctypes.windll.user32.LockWorkStation())


def open_special_folder(name):
    folder_map = {
        "downloads": Path.home()/"Downloads", "documents": Path.home()/"Documents",
        "desktop": Path.home()/"Desktop", "pictures": Path.home()/"Pictures",
        "music": Path.home()/"Music", "videos": Path.home()/"Videos",
    }
    path = folder_map.get(name)
    if not path: return None
    subprocess.Popen(["explorer.exe", str(path)])
    return path


def try_handle_command(text):
    global pending_confirmation
    """
    Checks if the text matches a known local command (time, date, open app/site).
    Returns a response string if handled locally, or None if it should go to Gemini instead.
    """
    t = text.lower().strip()

    # ---- Natural computer controls ----
    if t in {"volume up", "turn volume up", "increase volume", "louder"}:
        return "Volume increased." if volume_up() else "I can't change the volume here."
    if t in {"volume down", "turn volume down", "decrease volume", "quieter"}:
        return "Volume decreased." if volume_down() else "I can't change the volume here."
    if t in {"mute", "mute volume", "turn volume off"}:
        return "Volume muted." if volume_mute() else "I can't mute the volume here."
    if t in {"play", "pause", "play music", "pause music"}:
        media_key(0xB3); return "Done."
    if t in {"next track", "next song", "skip song", "skip track"}:
        media_key(0xB0); return "Skipping to the next track."
    if t in {"previous track", "previous song", "go back a song"}:
        media_key(0xB1); return "Going to the previous track."
    if t in {"show desktop", "minimize everything", "minimize all windows"}:
        return "Done." if show_desktop() else "I can't control the desktop here."
    if t in {"lock computer", "lock pc", "lock my computer"}:
        threading.Timer(0.15, lock_pc).start()
        return "Locking the computer."
    for folder_name in ("downloads", "documents", "desktop", "pictures", "music", "videos"):
        if t in {f"open {folder_name}", f"open my {folder_name}", f"show {folder_name}"}:
            path = open_special_folder(folder_name)
            return f"Opening {folder_name}." if path else f"I couldn't open {folder_name}."

    # ---- Desktop / system commands ----
    if t in {"take screenshot", "take a screenshot", "screenshot", "capture screen", "capture my screen"}:
        try:
            path = take_screenshot()
            return f"Screenshot saved as {path.name}."
        except Exception as e:
            return f"I couldn't take the screenshot: {e}"

    close_match = re.match(r"^(?:close|quit|exit)\s+(.+)$", text.strip(), re.IGNORECASE)
    if close_match:
        result = close_application(close_match.group(1))
        if result is not None:
            return result

    if t in {"list running apps", "show running apps", "what apps are running", "what is running"}:
        return system_tasklist()

    # ---- Persistent memory ----

    # Direct identity memory: "my name is ..." / "I am ..."
    name_match = re.match(
        r"^(?:my name is|i am|i'm)\s+(.+)$",
        text.strip(),
        flags=re.IGNORECASE,
    )
    if name_match:
        name = name_match.group(1).strip(" .!?\"")
        if name:
            # Keep the latest name and remove older name entries.
            forget_memory(search_text="my name is")
            remember(f"My name is {name}.")
            return f"Got it. I’ll remember your name is {name}."

    # Explicit memory request. Accept a couple of common speech-recognition
    # misspellings of "remember" as well.
    remember_match = re.match(
        r"^(?:remember|remeber|rember|save to memory|remember that)\s+(.+)$",
        text.strip(),
        flags=re.IGNORECASE,
    )
    if remember_match:
        item = remember_match.group(1).strip(" .")

        # Turn "my name is Prajwal" into a clean identity memory.
        item_name = re.match(
            r"^my name is\s+(.+)$", item, flags=re.IGNORECASE
        )
        if item_name:
            name = item_name.group(1).strip(" .!?\"")
            forget_memory(search_text="my name is")
            remember(f"My name is {name}.")
            return f"Got it. I’ll remember your name is {name}."

        if remember(item):
            return f"I’ll remember that: {item}"
        return "I couldn’t save that memory."

    # Deterministic identity retrieval so Gemini is not responsible for
    # remembering the user's name.
    if t in {
        "what's my name",
        "what is my name",
        "do you know my name",
        "do you remember my name",
        "remember my name",
    }:
        rows = get_memories(query="my name is", limit=1)
        if rows:
            saved = rows[0][1]
            match = re.search(r"my name is\s+(.+?)\.?$", saved, re.IGNORECASE)
            if match:
                return f"Your name is {match.group(1).strip()}."
        return "I don’t have your name saved yet. Tell me, for example, ‘my name is Prajwal’."

    if t in {
        "what do you remember",
        "what do you remember about me",
        "show my memories",
        "list my memories",
        "what memories do you have",
    }:
        rows = get_memories(limit=12)
        if not rows:
            return "I don’t have any saved memories yet."
        return "Here are my saved memories: " + "; ".join(
            f"{idx}. {memory}" for idx, (_, memory) in enumerate(rows, 1)
        )

    forget_match = re.match(
        r"^forget(?: that)?\s+(.+)$",
        text.strip(),
        flags=re.IGNORECASE,
    )
    if forget_match:
        target = forget_match.group(1).strip(" .")
        removed = forget_memory(search_text=target)
        if removed:
            return f"I forgot {removed} saved memory item(s) matching that."
        return "I couldn’t find a saved memory matching that."

    if t in {"forget everything", "forget all memories", "clear memory", "clear all memories"}:
        removed = forget_memory()
        return f"Cleared {removed} saved memory item(s)."

    # ---- Safety-gated power controls ----
    if t in {"shutdown", "shut down the computer", "turn off the pc", "power off"}:
        pending_confirmation = "shutdown"
        return "That will shut down the computer. Say 'confirm shutdown' if you want me to continue."
    if t in {"restart", "restart computer", "reboot", "reboot the pc"}:
        pending_confirmation = "restart"
        return "That will restart the computer. Say 'confirm restart' if you want me to continue."
    if t in {"cancel", "cancel that", "never mind"} and pending_confirmation:
        pending_confirmation = None
        return "Cancelled."
    if t == "confirm shutdown" and pending_confirmation == "shutdown":
        pending_confirmation = None
        subprocess.Popen(["shutdown", "/s", "/t", "5"])
        return "Shutdown confirmed."
    if t == "confirm restart" and pending_confirmation == "restart":
        pending_confirmation = None
        subprocess.Popen(["shutdown", "/r", "/t", "5"])
        return "Restart confirmed."

    # ---- Time ----
    if "what time" in t or "current time" in t or "tell me the time" in t:
        now = datetime.datetime.now().strftime("%I:%M %p")
        return f"It's currently {now}."

    # ---- Date ----
    if "what date" in t or "today's date" in t or "what is the date" in t or "what day is it" in t:
        today = datetime.datetime.now().strftime("%A, %B %d, %Y")
        return f"Today is {today}."

    # ---- Volume Control ----
    if "volume up" in t or "increase volume" in t or "turn up the volume" in t:
        pyautogui.press("volumeup")
        return "Volume increased."
    ...

    # ---- Screenshot ----
    if "take a screenshot" in t or "screenshot" in t or "capture screen" in t:
        screenshot = pyautogui.screenshot()
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(os.path.expanduser("~"), "Pictures", f"ultron_screnshot_{timestamp}.png")
        screenshot.save(save_path)
        return f"Screenshot saved to {save_path}"

    # ---- Open app or website ----
    if t.startswith("open ") or t.startswith("launch ") or t.startswith("start ") or t.startswith("run "):
        target = t.split(" ", 1)[1].strip()

        for name, command in COMMON_APPS.items():
            if name in target:
                try:
                    if command.startswith("start "):
                        os.system(command)
                    else:
                        subprocess.Popen(command)
                    return f"Opening {name}."
                except Exception as e:
                    return f"I couldn't open {name}: {e}"

        # If it looks like a website, open it in the browser
        if "." in target:
            url = target if target.startswith("http") else f"https://{target}"
            webbrowser.open(url)
            return f"Opening {target}."

        return None  # unrecognized target, let Gemini try to respond instead

    if t.startswith("search web for "):
        query = text[len("search web for "):].strip()
        if query:
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(query))
            return f"Searching the web for {query}."

    return None  # not a recognized command


def think(user_text):
    try:
        memories = memory_context(user_text)
        context = system_prompt + " Be proactive and concise. Never claim an action happened unless this program actually performed it."
        if memories:
            context += f"\n\nRelevant persistent memory:\n{memories}"
        if conversation_history:
            recent = "\n".join(f"{role}: {content}" for role, content in conversation_history[-CONVERSATION_LIMIT:])
            context += f"\n\nRecent conversation:\n{recent}"
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=f"{context}\n\nUser: {user_text}"
        )
        return response.text.strip()
    except Exception as e:
        print("BRAIN ERROR:", e)
        return "Sorry, I couldn't reach my brain just now. Try again in a moment."


async def _generate_speech(text, path):
    communicate = edge_tts.Communicate(text, voice=current_voice, rate=current_rate, pitch=current_pitch)
    await communicate.save(path)


def apply_robotic_filter(samples, sample_rate):
    """Ring-modulation + light distortion + short echo for a metallic, mechanical tone."""
    # Ring modulation: multiply the voice by a low-frequency carrier wave.
    # Blending 60% processed / 40% original keeps it intelligible while sounding metallic.
    t = np.arange(len(samples)) / sample_rate
    carrier_freq = 35  # Hz - lower = more warble, higher = more buzzy
    carrier = np.sin(2 * np.pi * carrier_freq * t)
    modulated = samples * (0.55 + 0.45 * carrier)
    blended = 0.4 * samples + 0.6 * modulated

    # Light distortion for grit
    blended = np.clip(blended * 1.4, -1.0, 1.0)

    # Short slap-back echo for a more commanding, spacious feel
    delay_samples = int(0.07 * sample_rate)
    echo = np.zeros_like(blended)
    echo[delay_samples:] = blended[:-delay_samples] * 0.25
    result = blended + echo

    return np.clip(result, -1.0, 1.0)


def speak(text):
    unique_path = os.path.join(
        tempfile.gettempdir(), f"ultron_speech_{uuid.uuid4().hex}.mp3"
    )
    try:
        asyncio.run(_generate_speech(text, unique_path))

        if robotic_filter_on:
            samples, sample_rate = sf.read(unique_path, dtype="float32")
            if samples.ndim > 1:
                samples = samples.mean(axis=1)  # mix down to mono if needed
            processed = apply_robotic_filter(samples, sample_rate)
            sd.play(processed, sample_rate)
            sd.wait()
        else:
            playsound(unique_path)
    except Exception as e:
        print("SPEECH ERROR:", e)
        # Fall back to plain playback if filtering failed for any reason
        try:
            playsound(unique_path)
        except Exception:
            pass
    finally:
        if os.path.exists(unique_path):
            os.remove(unique_path)


# ---------- Wake word background listener ----------

wake_stop_event = threading.Event()
wake_thread = None
wake_active = False
AUTO_WAKE = os.getenv("AUTO_WAKE", "1") not in {"0", "false", "no"}


def wake_word_loop(stop_event):
    """Jarvis-style wake flow: wake -> optional same-sentence command -> listen."""
    while not stop_event.is_set():
        if is_busy:
            time.sleep(0.15)
            continue
        try:
            audio = _record_audio(1.8)
            text = _transcribe_audio(audio, wake=True)
            if text:
                print("WAKE LOOP HEARD:", text)
            if not _wake_word_detected(text):
                continue

            remainder = _remove_wake_word(text)
            if remainder:
                app.after(0, lambda t=remainder: handle_message(t))
                continue

            app.after(0, lambda: set_status("listening", True))
            time.sleep(0.25)
            app.after(0, lambda: add_turn("Ultron", "Yes?", system=True))
            speak("Yes?")
            command_text = listen_after_wake()
            if command_text:
                app.after(0, lambda t=command_text: handle_message(t))
            else:
                app.after(0, lambda: set_status("ready", False))
        except Exception as exc:
            print("WAKE LISTENER ERROR:", exc)
            time.sleep(0.5)


# ================= ULTRON // CINEMATIC HUD GUI =================

ctk.set_appearance_mode("dark")

# ---------- Reference-inspired palette ----------
BG = "#03020a"
BG_2 = "#070511"
PANEL = "#0a0815"
PANEL_2 = "#100b20"
GRID = "#171126"
GRID_2 = "#21163a"
VIOLET = "#9b5cff"
VIOLET_BRIGHT = "#d8b4fe"
VIOLET_DIM = "#4b2678"
CYAN = "#62e8ff"
CYAN_DIM = "#17576a"
MAGENTA = "#ef65ff"
TEXT = "#f7f3ff"
MUTED = "#69617e"
GREEN = "#62f2c2"
RED = "#ff6688"

app = ctk.CTk()
app.title("ULTRON // JARVIS-STYLE NEURAL SYSTEM")
app.geometry("1280x800")
app.minsize(1050, 700)
app.configure(fg_color=BG)

# ---------- Helpers ----------
def blend(c1, c2, t):
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2],16), int(c1[2:4],16), int(c1[4:6],16)
    r2, g2, b2 = int(c2[0:2],16), int(c2[2:4],16), int(c2[4:6],16)
    return f"#{int(r1+(r2-r1)*t):02x}{int(g1+(g2-g1)*t):02x}{int(b1+(b2-b1)*t):02x}"

def spaced(s):
    return " ".join(list(s.upper()))

# ---------- Header ----------
header = ctk.CTkFrame(app, fg_color="transparent", height=54)
header.pack(fill="x", padx=22, pady=(14, 0))
header.pack_propagate(False)

left_brand = ctk.CTkFrame(header, fg_color="transparent")
left_brand.pack(side="left", fill="y")

ctk.CTkLabel(
    left_brand, text="U L T R O N",
    font=("Consolas", 20, "bold"), text_color=TEXT
).pack(side="left")

ctk.CTkLabel(
    left_brand, text="  /  NEURAL SYSTEM",
    font=("Consolas", 9), text_color=CYAN
).pack(side="left", pady=(7, 0))

clock_label = ctk.CTkLabel(
    header, text="00:00:00", font=("Consolas", 11), text_color=MUTED
)
clock_label.pack(side="right", padx=(14, 0), pady=10)

system_badge = ctk.CTkLabel(
    header, text="●  SYSTEM ONLINE",
    font=("Consolas", 9, "bold"), text_color=GREEN
)
system_badge.pack(side="right", pady=10)

def tick_clock():
    clock_label.configure(text=datetime.datetime.now().strftime("%H:%M:%S"))
    app.after(1000, tick_clock)

# ---------- Main 3-column dashboard ----------
main = ctk.CTkFrame(app, fg_color="transparent")
main.pack(fill="both", expand=True, padx=22, pady=(2, 16))

left = ctk.CTkFrame(main, fg_color="transparent", width=235)
left.pack(side="left", fill="y", padx=(0, 10))
left.pack_propagate(False)

center = ctk.CTkFrame(main, fg_color="transparent")
center.pack(side="left", fill="both", expand=True, padx=5)

right = ctk.CTkFrame(main, fg_color="transparent", width=285)
right.pack(side="right", fill="y", padx=(10, 0))
right.pack_propagate(False)

# ---------- Reusable card ----------
def card(parent):
    f = ctk.CTkFrame(
        parent, fg_color=PANEL, corner_radius=6,
        border_width=1, border_color=GRID_2
    )
    return f

def card_title(parent, title, code=""):
    row = ctk.CTkFrame(parent, fg_color="transparent", height=31)
    row.pack(fill="x", padx=12, pady=(8, 0))
    row.pack_propagate(False)
    ctk.CTkLabel(
        row, text=title, font=("Consolas", 9, "bold"), text_color=CYAN
    ).pack(side="left")
    if code:
        ctk.CTkLabel(
            row, text=code, font=("Consolas", 8), text_color=MUTED
        ).pack(side="right")
    ctk.CTkFrame(parent, height=1, fg_color=GRID).pack(fill="x", padx=12)

# ---------- LEFT: system metrics ----------
metrics = card(left)
metrics.pack(fill="x", pady=(0, 10))
card_title(metrics, "SYSTEM TELEMETRY", "SYS_029")

metric_rows = [
    ("CORE STATUS", "ONLINE", GREEN),
    ("NEURAL LINK", "98.7%", CYAN),
    ("AUDIO INPUT", "READY", GREEN),
    ("PROCESS LOAD", "32.4%", VIOLET_BRIGHT),
    ("VOICE OUTPUT", "ACTIVE", CYAN),
]

for label, value, col in metric_rows:
    row = ctk.CTkFrame(metrics, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=5)
    ctk.CTkLabel(row, text=label, font=("Consolas", 8), text_color=MUTED).pack(side="left")
    ctk.CTkLabel(row, text=value, font=("Consolas", 8, "bold"), text_color=col).pack(side="right")

# bars
for label, value in [("MEMORY", 0.68), ("NEURAL", 0.91), ("AUDIO", 0.43)]:
    row = ctk.CTkFrame(metrics, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=(2, 5))
    ctk.CTkLabel(row, text=label, font=("Consolas", 7), text_color=MUTED).pack(anchor="w")
    bar = ctk.CTkProgressBar(
        row, height=4, fg_color=GRID, progress_color=VIOLET
    )
    bar.set(value)
    bar.pack(fill="x", pady=(2, 0))

# ---------- LEFT: mini waveform ----------
wave_card = card(left)
wave_card.pack(fill="x", pady=(0, 10))
card_title(wave_card, "SIGNAL MONITOR", "LIVE")

wave_canvas = tk.Canvas(wave_card, height=100, bg=PANEL, highlightthickness=0)
wave_canvas.pack(fill="x", padx=10, pady=10)
wave_phase = 0

def draw_wave():
    global wave_phase
    wave_canvas.delete("all")
    w = max(wave_canvas.winfo_width(), 190)
    h = 100

    for x in range(0, w, 30):
        wave_canvas.create_line(x, 0, x, h, fill=GRID)
    for y in range(10, h, 20):
        wave_canvas.create_line(0, y, w, y, fill=GRID)

    pts = []
    for x in range(0, w + 1, 4):
        amp = 10 + 5 * abs(np.sin(x * 0.025 + wave_phase))
        y = h/2 + np.sin(x*0.11 + wave_phase)*amp
        pts.extend((x, y))
    wave_canvas.create_line(*pts, fill=CYAN, width=2, smooth=True)
    wave_phase += 0.17
    app.after(45, draw_wave)

# ---------- LEFT: system feed ----------
feed = card(left)
feed.pack(fill="both", expand=True)
card_title(feed, "SYSTEM FEED", "STREAM")

feed_box = ctk.CTkTextbox(
    feed, fg_color=BG_2, text_color=MUTED,
    border_width=0, corner_radius=0,
    font=("Consolas", 8), wrap="word"
)
feed_box.pack(fill="both", expand=True, padx=8, pady=8)
feed_box.insert("end",
    "[16:44:01] CORE handshake accepted\n"
    "[16:44:02] Neural matrix synchronized\n"
    "[16:44:03] Voice subsystem ready\n"
    "[16:44:04] Waiting for command...\n"
)
feed_box.configure(state="disabled")

# ---------- CENTER: main holographic core ----------
core_header = ctk.CTkFrame(center, fg_color="transparent", height=30)
core_header.pack(fill="x")
core_header.pack_propagate(False)

ctk.CTkLabel(
    core_header, text="BIO-MECHANICAL ANALYSIS",
    font=("Consolas", 9, "bold"), text_color=MUTED
).pack(side="left", pady=4)

ctk.CTkLabel(
    core_header, text="TS_029.23    B3/6    SK3_RG_821",
    font=("Consolas", 8), text_color=VIOLET_BRIGHT
).pack(side="right", pady=4)

core_card = card(center)
core_card.pack(fill="both", expand=True)

CORE_W, CORE_H = 610, 470
core_canvas = tk.Canvas(
    core_card, width=CORE_W, height=CORE_H,
    bg=BG_2, highlightthickness=0
)
core_canvas.pack(fill="both", expand=True, padx=5, pady=5)

status_word = ctk.CTkLabel(
    center, text=spaced("ready"),
    font=("Consolas", 10, "bold"), text_color=MUTED
)
status_word.pack(pady=(6, 0))

current_state = "ready"
animation_frame = 0

def set_status(text, active=False):
    global current_state
    if text in ("ready", "listening", "thinking", "speaking"):
        current_state = text
    status_word.configure(
        text=spaced(text),
        text_color=CYAN if active or text != "ready" else MUTED
    )

def draw_core_grid(c, w, h, spacing):
    for x in range(0, w, spacing):
        c.create_line(x, 0, x, h, fill="#0e0b1c")
    for y in range(0, h, spacing):
        c.create_line(0, y, w, y, fill="#0e0b1c")

def tick_ring(c, cx, cy, radius, rotation, count, color):
    for i in range(count):
        a = 2*np.pi*i/count + rotation
        length = 10 if i % 6 == 0 else 4
        x1, y1 = cx + radius*np.cos(a), cy + radius*np.sin(a)
        x2, y2 = cx + (radius-length)*np.cos(a), cy + (radius-length)*np.sin(a)
        c.create_line(x1, y1, x2, y2, fill=color, width=1)

def bracket(c, x, y, sx, sy):
    c.create_line(x, y, x+sx*18, y, fill=CYAN_DIM, width=2)
    c.create_line(x, y, x, y+sy*18, fill=CYAN_DIM, width=2)

def draw_spine(c, cx, cy, t, scale):
    # A more recognizable synthetic vertebra/spine structure.
    for i, y in enumerate(np.linspace(cy - 120 * scale, cy + 120 * scale, 15)):
        wobble = np.sin(t*0.035 + i*0.55) * 7 * scale
        x = cx + wobble
        width = (25 if i < 5 or i > 10 else 31) * scale
        half_height = max(3, 7 * scale)
        c.create_oval(
            x-width, y-half_height, x+width, y+half_height,
            outline=blend(VIOLET, BG_2, 0.18),
            width=1
        )
        c.create_line(
            x-width+4, y, x+width-4, y,
            fill=VIOLET_BRIGHT if i % 2 == 0 else VIOLET_DIM
        )
        node = max(2, 5 * scale)
        c.create_oval(x-node, y-node, x+node, y+node, fill=CYAN_DIM, outline="")

def draw_core():
    # Render only after Tk has calculated the canvas's on-screen size.
    # This prevents a fixed 610x470 drawing from being clipped on resize.
    w, h = core_canvas.winfo_width(), core_canvas.winfo_height()
    if w <= 1 or h <= 1:
        return

    core_canvas.delete("all")
    cx, cy = w/2, h/2
    t = animation_frame
    scale = max(0.35, min(w / CORE_W, h / CORE_H))

    draw_core_grid(core_canvas, w, h, max(16, int(35 * scale)))

    # Faint circular target geometry
    for rr in (78, 112, 145):
        rr *= scale
        core_canvas.create_oval(
            cx-rr, cy-rr, cx+rr, cy+rr,
            outline=blend(VIOLET_DIM, BG_2, 0.15), width=1
        )

    # Rotating rings
    ring_col = CYAN if current_state == "listening" else VIOLET
    tick_ring(core_canvas, cx, cy, 164 * scale, t*0.006, 72, VIOLET_DIM)
    tick_ring(core_canvas, cx, cy, 150 * scale, -t*0.010, 48, ring_col)

    # Scanner sweep
    sweep = (t*3.5) % 360
    rad = np.deg2rad(sweep)
    x2, y2 = cx + 180*scale*np.cos(rad), cy + 180*scale*np.sin(rad)
    core_canvas.create_line(cx, cy, x2, y2, fill=CYAN_DIM, width=1)

    # Rotating arcs
    core_canvas.create_arc(
        cx-185*scale, cy-185*scale, cx+185*scale, cy+185*scale,
        start=sweep, extent=52, style="arc", outline=CYAN, width=2
    )
    core_canvas.create_arc(
        cx-185*scale, cy-185*scale, cx+185*scale, cy+185*scale,
        start=sweep+180, extent=38, style="arc", outline=MAGENTA, width=1
    )

    # Central holographic body
    draw_spine(core_canvas, cx, cy, t, scale)

    # Core pulse
    pulse = 5*np.sin(t*0.09)
    if current_state == "listening":
        pulse += 10*abs(np.sin(t*0.25))
    elif current_state == "speaking":
        pulse += 13*abs(np.sin(t*0.34))

    r = (37 + pulse) * scale
    for rr, fade in [(r*2.0, .90), (r*1.55, .72), (r*1.20, .45)]:
        core_canvas.create_oval(
            cx-rr, cy-rr, cx+rr, cy+rr,
            fill=blend(VIOLET, BG_2, fade), outline=""
        )

    core_canvas.create_oval(
        cx-r*.55, cy-r*.55, cx+r*.55, cy+r*.55,
        fill=blend(VIOLET_BRIGHT, VIOLET, .40),
        outline=CYAN, width=2
    )
    core_canvas.create_text(
        cx, cy, text="U", fill=TEXT,
        font=("Consolas", 26, "bold")
    )

    # Targeting brackets
    off = 205 * scale
    bracket(core_canvas, cx-off, cy-off, 1, 1)
    bracket(core_canvas, cx+off, cy-off, -1, 1)
    bracket(core_canvas, cx-off, cy+off, 1, -1)
    bracket(core_canvas, cx+off, cy+off, -1, -1)

    # Technical annotations
    annotations = [
        ("SKIN_TISSUE_RESAMPLING", 18, 22),
        ("NEURAL_DENSITY  98.7%", 18, h-25),
        ("CORE_LOCK", w-108, 22),
        ("NTX / 04", w-76, h-25),
        ("SCAN", cx+175*scale, cy-12),
    ]
    for text, x, y in annotations:
        core_canvas.create_text(
            x, y, text=text, anchor="w",
            fill=MUTED, font=("Consolas", 7)
        )

def animate_core():
    global animation_frame
    try:
        if not core_canvas.winfo_exists():
            return
        animation_frame += 1
        draw_core()
    except tk.TclError:
        return
    app.after(40, animate_core)

# ---------- RIGHT: diagnostics ----------
diag = card(right)
diag.pack(fill="x", pady=(0, 10))
card_title(diag, "DIAGNOSTICS", "AUTO")

diag_items = [
    ("SPEECH", GREEN),
    ("MEMORY", GREEN),
    ("WAKE LINK", VIOLET_BRIGHT),
    ("GEMINI LINK", GREEN),
]

# Persistent-memory counter
memory_counter_label = ctk.CTkLabel(
    diag, text=f"LOCAL MEMORY  {memory_count()}",
    font=("Consolas", 7), text_color=VIOLET_BRIGHT
)

for label, col in diag_items:
    row = ctk.CTkFrame(diag, fg_color="transparent")
    row.pack(fill="x", padx=12, pady=5)
    ctk.CTkLabel(row, text="◆", font=("Consolas", 7), text_color=col).pack(side="left", padx=(0,7))
    ctk.CTkLabel(row, text=label, font=("Consolas", 8), text_color=MUTED).pack(side="left")
    ctk.CTkLabel(row, text="NOMINAL", font=("Consolas", 7, "bold"), text_color=col).pack(side="right")

memory_counter_label.pack(anchor="w", padx=12, pady=(4, 10))

# ---------- RIGHT: communication log ----------
chat_card = card(right)
chat_card.pack(fill="both", expand=True, pady=(0, 10))
card_title(chat_card, "COMMUNICATION", "CHANNEL 01")

log_box = ctk.CTkTextbox(
    chat_card, fg_color=BG_2, text_color=TEXT,
    corner_radius=4, border_width=1, border_color=GRID,
    font=("Segoe UI", 10), wrap="word"
)
log_box.pack(fill="both", expand=True, padx=9, pady=9)
log_box.configure(state="disabled")

log_box._textbox.tag_config("user_tag", foreground=VIOLET_BRIGHT, font=("Consolas", 8, "bold"))
log_box._textbox.tag_config("ultron_tag", foreground=CYAN, font=("Consolas", 8, "bold"))
log_box._textbox.tag_config("body_tag", foreground=TEXT, font=("Segoe UI", 9))
log_box._textbox.tag_config("system_tag", foreground=MUTED, font=("Segoe UI", 8, "italic"))
log_box._textbox.tag_config("time_tag", foreground="#443b59", font=("Consolas", 7))

def add_turn(who, text, system=False):
    log_box.configure(state="normal")
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    if system:
        log_box._textbox.insert("end", f"// {text}\n\n", "system_tag")
    else:
        tag = "user_tag" if who == "You" else "ultron_tag"
        log_box._textbox.insert("end", f"{ts}  ", "time_tag")
        log_box._textbox.insert("end", f"{who}\n", tag)
        log_box._textbox.insert("end", f"{text}\n\n", "body_tag")
    log_box.configure(state="disabled")
    log_box.see("end")

# ---------- Bottom command console ----------
console = card(right)
console.pack(fill="x")

input_frame = ctk.CTkFrame(console, fg_color="transparent")
input_frame.pack(fill="x", padx=8, pady=8)

def on_send():
    text = text_entry.get().strip()
    text_entry.delete(0, "end")
    handle_message(text)

def on_talk():
    set_status("listening", True)
    mic_btn.configure(state="disabled")
    threading.Thread(target=_listen_from_button, daemon=True).start()

def _listen_from_button():
    """Record off the Tk thread so the HUD keeps animating while listening."""
    try:
        heard = listen(duration=7)
    except Exception as exc:
        print("MIC ERROR:", exc)
        heard = ""
    app.after(0, lambda: _finish_button_listen(heard))

def _finish_button_listen(heard):
    mic_btn.configure(state="normal")
    if not heard:
        set_status("ready", False)
        return
    handle_message(heard)

mic_btn = ctk.CTkButton(
    input_frame, text="◉", width=35, height=34, command=on_talk,
    fg_color=PANEL_2, hover_color=VIOLET_DIM, text_color=CYAN,
    font=("Consolas", 14, "bold"), corner_radius=3,
    border_width=1, border_color=GRID_2
)
mic_btn.pack(side="left", padx=(0, 6))

text_entry = ctk.CTkEntry(
    input_frame, placeholder_text="ENTER COMMAND...",
    fg_color=BG_2, text_color=TEXT, border_color=GRID_2,
    font=("Consolas", 9), height=34
)
text_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
text_entry.bind("<Return>", lambda e: on_send())

send_btn = ctk.CTkButton(
    input_frame, text="↵", width=35, height=34, command=on_send,
    fg_color=VIOLET, hover_color="#7c3aed", text_color=BG,
    font=("Consolas", 14, "bold"), corner_radius=3
)
send_btn.pack(side="left")



# ---------- Wake + settings row ----------
control_row = ctk.CTkFrame(right, fg_color="transparent")
control_row.pack(fill="x")

wake_active = False
wake_thread = None
wake_stop_event = threading.Event()

def toggle_wake_word():
    global wake_thread, wake_active
    if not wake_active:
        wake_stop_event.clear()
        wake_thread = threading.Thread(
            target=wake_word_loop, args=(wake_stop_event,), daemon=True
        )
        wake_thread.start()
        wake_active = True
        wake_btn.configure(text="WAKE // ON", fg_color=VIOLET, text_color=BG)
        add_turn("Ultron", 'Wake link enabled. Say "Ultron" to start.', system=True)
    else:
        wake_stop_event.set()
        wake_active = False
        wake_btn.configure(text="WAKE // OFF", fg_color=PANEL_2, text_color=MUTED)
        add_turn("Ultron", "Wake link disabled.", system=True)

wake_btn = ctk.CTkButton(
    control_row, text="WAKE // OFF", command=toggle_wake_word,
    fg_color=PANEL_2, hover_color=VIOLET_DIM, text_color=MUTED,
    font=("Consolas", 8, "bold"), height=30, corner_radius=3,
    border_width=1, border_color=GRID_2
)
wake_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))

settings_visible = False
settings_frame = ctk.CTkFrame(
    app, fg_color=PANEL, corner_radius=6,
    border_width=1, border_color=GRID_2
)

def toggle_settings():
    global settings_visible
    settings_visible = not settings_visible
    if settings_visible:
        settings_frame.pack(fill="x", padx=22, pady=(0, 10), before=main)
        settings_btn.configure(text_color=MAGENTA)
    else:
        settings_frame.pack_forget()
        settings_btn.configure(text_color=CYAN)

settings_btn = ctk.CTkButton(
    control_row, text="⚙", width=38, height=30,
    command=toggle_settings, fg_color=PANEL_2,
    hover_color=VIOLET_DIM, text_color=CYAN,
    font=("Segoe UI Symbol", 15), corner_radius=3,
    border_width=1, border_color=GRID_2
)
settings_btn.pack(side="right")

# ---------- Settings contents ----------
settings_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
settings_inner.pack(fill="x", padx=12, pady=9)

ctk.CTkLabel(
    settings_inner, text="VOICE", font=("Consolas", 8, "bold"), text_color=MUTED
).pack(side="left", padx=(4, 7))

voice_label = next((name for name, value in VOICE_OPTIONS.items() if value == current_voice), "Ryan (UK, deep)")
voice_var = ctk.StringVar(value=voice_label)

def on_voice_change(choice):
    global current_voice
    current_voice = VOICE_OPTIONS[choice]
    save_setting("voice", current_voice)

voice_dropdown = ctk.CTkOptionMenu(
    settings_inner, values=list(VOICE_OPTIONS.keys()),
    variable=voice_var, command=on_voice_change,
    fg_color=PANEL_2, button_color=VIOLET_DIM,
    button_hover_color=VIOLET, text_color=TEXT,
    font=("Segoe UI", 9), width=145
)
voice_dropdown.pack(side="left", padx=(0, 12))

def test_voice():
    set_status("speaking", True)
    threading.Thread(target=_test_voice_worker, daemon=True).start()

def _test_voice_worker():
    speak("Ultron neural interface online.")
    app.after(0, lambda: set_status("ready", False))

ctk.CTkButton(
    settings_inner, text="TEST", width=48, height=26, command=test_voice,
    fg_color=PANEL_2, hover_color=VIOLET_DIM, text_color=CYAN,
    font=("Consolas", 8), corner_radius=3,
    border_width=1, border_color=GRID_2
).pack(side="left", padx=(0, 18))

ctk.CTkLabel(
    settings_inner, text="RATE", font=("Consolas", 8, "bold"), text_color=MUTED
).pack(side="left")

rate_value_label = ctk.CTkLabel(
    settings_inner, text=current_rate, font=("Consolas", 8), text_color=CYAN, width=35
)

def on_rate_change(value):
    global current_rate
    pct = int(float(value))
    current_rate = f"{pct:+d}%"
    save_setting("rate", current_rate)
    rate_value_label.configure(text=current_rate)

rate_slider = ctk.CTkSlider(
    settings_inner, from_=-50, to=50, command=on_rate_change,
    fg_color=PANEL_2, progress_color=VIOLET,
    button_color=VIOLET, button_hover_color=VIOLET, width=120
)
rate_slider.set(int(current_rate.rstrip("%")))
rate_slider.pack(side="left", padx=6)
rate_value_label.pack(side="left", padx=(0, 15))

ctk.CTkLabel(
    settings_inner, text="PITCH", font=("Consolas", 8, "bold"), text_color=MUTED
).pack(side="left")

pitch_value_label = ctk.CTkLabel(
    settings_inner, text=current_pitch, font=("Consolas", 8), text_color=CYAN, width=40
)

def on_pitch_change(value):
    global current_pitch
    hz = int(float(value))
    current_pitch = f"{hz:+d}Hz"
    save_setting("pitch", current_pitch)
    pitch_value_label.configure(text=current_pitch)

pitch_slider = ctk.CTkSlider(
    settings_inner, from_=-80, to=-50, command=on_pitch_change,
    fg_color=PANEL_2, progress_color=VIOLET,
    button_color=VIOLET, button_hover_color=VIOLET, width=120
)
pitch_slider.set(int(current_pitch.rstrip("Hz")))
pitch_slider.pack(side="left", padx=6)
pitch_value_label.pack(side="left")

# ---------- Message handler ----------
def handle_message(user_text):
    """Start a response without blocking Tk's animation event loop."""
    global is_busy
    user_text = user_text.strip()
    if not user_text or is_busy:
        return

    is_busy = True
    add_turn("You", user_text)
    conversation_history.append(("User", user_text))
    conversation_history[:] = conversation_history[-CONVERSATION_LIMIT:]
    set_status("thinking", True)
    threading.Thread(target=_prepare_response, args=(user_text,), daemon=True).start()

def _prepare_response(user_text):
    """Run local commands and Gemini I/O away from the Tk thread."""
    try:
        local_reply = try_handle_command(user_text)
        reply = local_reply if local_reply is not None else think(user_text)
    except Exception as exc:
        print("RESPONSE ERROR:", exc)
        reply = "Sorry, something went wrong while preparing that response."
    app.after(0, lambda: _show_response(reply))

def _show_response(reply):
    """Update widgets only from the Tk thread, then play speech in a worker."""
    conversation_history.append(("Ultron", reply))
    conversation_history[:] = conversation_history[-CONVERSATION_LIMIT:]
    try:
        memory_counter_label.configure(text=f"LOCAL MEMORY  {memory_count()}")
    except Exception:
        pass
    add_turn("Ultron", reply)
    set_status("speaking", True)
    threading.Thread(target=_speak_response, args=(reply,), daemon=True).start()

def _speak_response(reply):
    global is_busy
    try:
        speak(reply)
    finally:
        is_busy = False
        app.after(0, lambda: set_status("ready", False))

# ---------- Startup ----------
add_turn("Ultron", "Neural interface synchronized. Say 'Ultron' when you need me.")
set_status("ready", False)

# Wait for Tk to calculate the widget sizes.  On some systems, drawing before
# mainloop creates the first core frame on a 1x1 canvas and leaves it blank or
# clipped until a later redraw.
app.after_idle(animate_core)
app.after_idle(draw_wave)
app.after_idle(tick_clock)

# JARVIS-style: begin listening for the wake word automatically unless disabled in .env.
if AUTO_WAKE:
    wake_stop_event.clear()
    wake_thread = threading.Thread(target=wake_word_loop, args=(wake_stop_event,), daemon=True)
    wake_thread.start()
    wake_active = True
    app.after(50, lambda: wake_btn.configure(text="WAKE // ON", fg_color=VIOLET, text_color=BG))

# ---------- System tray ----------
def create_tray_image():
    img = Image.new("RGB", (64, 64), color=BG)
    draw = ImageDraw.Draw(img)
    draw.ellipse((12, 12, 52, 52), fill=VIOLET)
    draw.ellipse((20, 20, 44, 44), fill=BG)
    draw.text((28, 23), "U", fill=CYAN)
    return img

def show_window(icon=None, item=None):
    app.after(0, lambda: (app.deiconify(), app.lift(), app.focus_force()))

def quit_app(icon=None, item=None):
    wake_stop_event.set()
    if tray_icon:
        tray_icon.stop()
    app.after(0, app.destroy)

def toggle_wake_word_from_tray(icon=None, item=None):
    app.after(0, toggle_wake_word)

def on_window_close():
    app.withdraw()

tray_icon = None

def run_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem("Show Ultron", show_window, default=True),
        pystray.MenuItem("Toggle Wake Word", toggle_wake_word_from_tray),
        pystray.MenuItem("Quit", quit_app),
    )
    tray_icon = pystray.Icon("Ultron", create_tray_image(), "Ultron", menu)
    tray_icon.run()

app.protocol("WM_DELETE_WINDOW", on_window_close)
threading.Thread(target=run_tray, daemon=True).start()

app.mainloop()
