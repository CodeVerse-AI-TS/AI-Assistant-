"""
N.O.V.A. - Nocturnal Offline Voice Anomaly
Push-to-talk local voice assistant with a chat window UI.

Pipeline: Hold SPACE -> speak -> release SPACE -> Whisper transcribes ->
          command check, or Ollama (local LLM) replies -> edge-tts speaks.

SETUP (run these once):
    pip install customtkinter faster-whisper sounddevice keyboard edge-tts pygame requests numpy

    Make sure Ollama is running and the model below has been pulled:
        ollama pull qwen2.5:7b-instruct-q4_K_M

    First run auto-downloads the Whisper model (needs internet once, then cached).
"""

import asyncio
import queue
import tempfile
import os
import sys
import glob
import subprocess
import webbrowser
import threading
import json
from datetime import datetime

import numpy as np
import requests
import sounddevice as sd
import keyboard
import edge_tts
import pygame
import customtkinter as ctk
import tkinter as tk
from faster_whisper import WhisperModel

# ---- Make Windows find the NVIDIA cuBLAS/cuDNN DLLs installed via pip ----
if sys.platform == "win32":
    for pkg in ("cublas", "cudnn"):
        matches = glob.glob(
            os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", pkg, "bin")
        )
        for path in matches:
            os.add_dll_directory(path)

# ---------------- CONFIG ----------------
WHISPER_MODEL_SIZE = "small"
WHISPER_DEVICE = "cuda"
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5:7b-instruct-q4_K_M"
TTS_VOICE = "en-US-AriaNeural"
SAMPLE_RATE = 16000
# Push-to-talk key is SPACE, bound directly to the NOVA window (see NovaApp
# below) — so it only fires when the window has focus, not system-wide.
HISTORY_FILE = "nova_history.json"
MAX_HISTORY_MESSAGES = 40  # trim what's sent to the LLM so old chats don't slow every reply down

APP_PATHS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "chrome": "chrome.exe",
    "vs code": "code",
    "visual studio code": "code",
    "spotify": "spotify.exe",
    "file explorer": "explorer.exe",
}
# -----------------------------------------

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are N.O.V.A. (Nocturnal Offline Voice Anomaly), a personal voice "
        "assistant. Keep answers short and conversational, since your replies "
        "are spoken aloud."
    ),
}


def load_history() -> list:
    """Loads saved conversation from disk, or starts fresh with just the system prompt."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and saved[0].get("role") == "system":
                saved[0] = SYSTEM_PROMPT  # keep prompt current even if it changes in code later
            else:
                saved.insert(0, SYSTEM_PROMPT)
            return saved
        except Exception:
            pass  # corrupted file, fall through to fresh history
    return [SYSTEM_PROMPT]


def save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] couldn't save history: {e}")


chat_history = load_history()

pygame.mixer.init()
audio_queue = queue.Queue()
ui_queue = queue.Queue()  # (sender, text) tuples pushed from worker thread to GUI


# ---------------- COMMANDS ----------------
def cmd_time(text): return f"It's {datetime.now().strftime('%I:%M %p')}."
def cmd_date(text): return f"Today is {datetime.now().strftime('%A, %B %d')}."


def cmd_open_app(text):
    for name, path in APP_PATHS.items():
        if name in text:
            try:
                subprocess.Popen(path, shell=True)
                return f"Opening {name}."
            except Exception as e:
                return f"Couldn't open {name}: {e}"
    return "I don't know that app. Add it to APP_PATHS in the code."


def cmd_web_search(text):
    for trigger in ("search for", "search", "google"):
        if trigger in text:
            query = text.split(trigger, 1)[1].strip()
            break
    else:
        query = text
    webbrowser.open(f"https://www.google.com/search?q={query}")
    return f"Searching for {query}."


def cmd_weather(text):
    city = text.split("in", 1)[1].strip() if " in " in text else ""
    url = f"https://wttr.in/{city}?format=%C+%t" if city else "https://wttr.in/?format=%C+%t"
    try:
        resp = requests.get(url, timeout=10)
        return f"It's currently {resp.text.strip()}" + (f" in {city}." if city else ".")
    except Exception:
        return "Couldn't fetch the weather right now."


def cmd_volume_up(text): keyboard.send("volume up"); return "Volume up."
def cmd_volume_down(text): keyboard.send("volume down"); return "Volume down."
def cmd_mute(text): keyboard.send("volume mute"); return "Muted."


def cmd_lock(text):
    subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
    return "Locking now."


def cmd_shutdown(text):
    subprocess.run("shutdown /s /t 30", shell=True)
    return "Shutting down in 30 seconds. Say cancel shutdown to stop it."


def cmd_cancel_shutdown(text):
    subprocess.run("shutdown /a", shell=True)
    return "Shutdown cancelled."


COMMANDS = [
    (["cancel shutdown"], cmd_cancel_shutdown),
    (["shut down my computer", "shutdown my computer", "shutdown the computer"], cmd_shutdown),
    (["lock my computer", "lock the computer", "lock pc"], cmd_lock),
    (["what time is it", "what's the time", "what is the time", "tell me the time", "current time", "time is it"], cmd_time),
    (["what's the date", "what is the date", "tell me the date", "today's date", "what day is it"], cmd_date),
    (["weather"], cmd_weather),
    (["volume up"], cmd_volume_up),
    (["volume down"], cmd_volume_down),
    (["mute"], cmd_mute),
    (["search for", "search ", "google "], cmd_web_search),
    (["open "], cmd_open_app),
]


def handle_command(text):
    lowered = text.lower()
    for phrases, handler in COMMANDS:
        if any(phrase in lowered for phrase in phrases):
            return handler(lowered)
    return None


# ---------------- AUDIO / BRAIN / VOICE ----------------
def audio_callback(indata, frames, time_info, status):
    audio_queue.put(bytes(indata))


def record_while_key_held(app: "NovaApp") -> np.ndarray:
    """Records while SPACE is held in the NOVA window (not a global hook —
    more reliable, since global key-hold detection needs elevated permissions
    on Windows and can silently miss the 'still held' state)."""
    app.space_press_event.wait()
    app.space_press_event.clear()
    app.space_release_event.clear()

    frames = []
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE, blocksize=8000, dtype="int16",
        channels=1, callback=audio_callback,
    ):
        while not app.space_release_event.is_set():
            try:
                frames.append(audio_queue.get(timeout=0.1))
            except queue.Empty:
                continue

    raw = b"".join(frames)
    audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    # Boost quiet recordings so Whisper has enough signal to work with —
    # scales the loudest point in the clip up to ~90% of max volume.
    peak = np.abs(audio_np).max() if audio_np.size else 0.0
    if 0 < peak < 0.9:
        audio_np = audio_np * (0.9 / peak)

    return audio_np


def transcribe(audio_np, whisper: WhisperModel) -> str:
    if audio_np.size == 0:
        return ""
    segments, _ = whisper.transcribe(audio_np, language="en", beam_size=5)
    return " ".join(segment.text.strip() for segment in segments).strip()


def ask_nova(user_text: str) -> str:
    chat_history.append({"role": "user", "content": user_text})
    try:
        # Always keep the system prompt, plus only the most recent messages —
        # keeps prompts fast even after weeks of saved conversation.
        trimmed = [chat_history[0]] + chat_history[-MAX_HISTORY_MESSAGES:]
        response = requests.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "messages": trimmed, "stream": False},
            timeout=60,
        )
        response.raise_for_status()
        reply = response.json()["message"]["content"]
        chat_history.append({"role": "assistant", "content": reply})
        save_history()
        return reply
    except requests.exceptions.ConnectionError:
        return "I can't reach my brain right now. Is Ollama running?"
    except Exception as e:
        return f"Something went wrong: {e}"


async def _speak_async(text: str):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(tmp_path)
    pygame.mixer.music.load(tmp_path)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)
    pygame.mixer.music.unload()
    os.remove(tmp_path)


def speak(text: str):
    asyncio.run(_speak_async(text))


# ---------------- WORKER THREAD ----------------
def process_message(app, text: str, speak_reply: bool = True):
    """Handles one user message (from voice OR typed text): checks for quit,
    checks commands, falls back to the LLM, updates the UI, and speaks the reply."""
    ui_queue.put(("you", text))

    if text.lower() in ("quit", "exit", "shut down", "goodbye"):
        ui_queue.put(("nova", "Going offline."))
        app.set_status("Offline")
        speak("Going offline.")
        return True  # signals "should exit"

    app.set_status("Thinking...")
    command_reply = handle_command(text)
    if command_reply is not None:
        chat_history.append({"role": "user", "content": text})
        chat_history.append({"role": "assistant", "content": command_reply})
        save_history()
        reply = command_reply
    else:
        reply = ask_nova(text)

    ui_queue.put(("nova", reply))
    if speak_reply:
        app.set_status("Speaking...")
        speak(reply)
    return False


def worker_loop(app):
    app.set_status("Loading Whisper model...")
    whisper = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type="int8_float16")

    # Show past conversation (if any) in the chat window before greeting
    for msg in chat_history[1:]:  # skip the system prompt
        sender = "you" if msg["role"] == "user" else "nova"
        ui_queue.put((sender, msg["content"]))

    app.set_status("Ready \u2014 hold SPACE to talk, or type below")
    ui_queue.put(("nova", "Nova online."))
    speak("Nova online.")

    while True:
        app.set_status("Hold SPACE to talk...")
        audio_np = record_while_key_held(app)

        app.set_status("Transcribing...")
        duration_sec = len(audio_np) / SAMPLE_RATE
        peak_level = float(np.abs(audio_np).max()) if audio_np.size else 0.0
        print(f"[DEBUG] duration={duration_sec:.2f}s peak_level={peak_level:.4f}")
        text = transcribe(audio_np, whisper)

        if not text:
            app.set_status("Didn't catch that \u2014 hold SPACE to talk")
            continue

        should_exit = process_message(app, text)
        if should_exit:
            break

        app.set_status("Hold SPACE to talk, or type below")


def text_worker(app, text: str):
    """Runs in its own thread so typed messages don't freeze the GUI while
    the LLM or a command is processing."""
    app.set_status("Thinking...")
    process_message(app, text)
    app.set_status("Ready \u2014 hold SPACE to talk, or type below")


# ---------------- GUI ----------------
# Palette — a dark "system readout" look built around NOVA's own concept
# (Nocturnal Offline Voice Anomaly), not a default chat-app blue theme.
BG_PRIMARY = "#0A0E17"        # near-black navy window background
BG_PANEL = "#10141F"          # slightly lighter panel background (top row, input row)
BUBBLE_NOVA = "#1B2233"       # NOVA's messages — dark slate panel
BUBBLE_NOVA_BORDER = "#3A3F63"  # subtle violet-grey border, like a HUD panel edge
BUBBLE_USER = "#6C5CE7"       # your messages — solid violet
TEXT_PRIMARY = "#E8EAF0"      # off-white body text
TEXT_DIM = "#7A8099"          # dimmed secondary text
ACCENT = "#7C5CFC"            # primary accent (buttons, focus)
ACCENT_HOVER = "#6A4CE0"
STATUS_COLORS = {
    "idle": "#5A6079",
    "listening": "#7C5CFC",
    "thinking": "#E0A64B",
    "speaking": "#3FD6B5",
    "offline": "#E06464",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")  # base theme; we override specific widget colors below


class NovaApp(ctk.CTk):
    COMPACT_SIZE = "420x600"
    FULL_SIZE = "900x700"
    COMPACT_WRAP = 280
    FULL_WRAP = 640
    COMPACT_FONT_SIZE = 13
    FULL_FONT_SIZE = 22

    def __init__(self):
        super().__init__()
        self.title("N.O.V.A.")
        self.geometry(self.COMPACT_SIZE)
        self.minsize(360, 480)
        self.configure(fg_color=BG_PRIMARY)
        self.is_compact = True
        self.bubble_wraplength = self.COMPACT_WRAP
        self.bubble_font_size = self.COMPACT_FONT_SIZE

        # Top row: status dot + status text + compact/full toggle
        self.top_row = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        self.top_row.pack(fill="x", padx=10, pady=(10, 4))

        self.status_dot = tk.Canvas(
            self.top_row, width=12, height=12, bg=BG_PANEL, highlightthickness=0
        )
        self.status_dot.pack(side="left", padx=(12, 6), pady=10)
        self._dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill=STATUS_COLORS["idle"], outline="")

        self.status_label = ctk.CTkLabel(
            self.top_row, text="STARTING UP", font=("Consolas", 12),
            text_color=TEXT_DIM, anchor="w",
        )
        self.status_label.pack(side="left", expand=True, fill="x", pady=10)

        self.size_toggle_button = ctk.CTkButton(
            self.top_row, text="⤢ Expand", width=90, height=26,
            font=("Segoe UI", 11), fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", corner_radius=8, command=self._toggle_window_size,
        )
        self.size_toggle_button.pack(side="right", padx=10, pady=8)

        # Text input row — pack this at the BOTTOM first, so it reserves its
        # space before the scrollable chat frame expands to fill everything else.
        self.input_frame = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=10)
        self.input_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        self.text_entry = ctk.CTkEntry(
            self.input_frame, placeholder_text="Type a message to NOVA...",
            fg_color=BG_PRIMARY, border_color=BUBBLE_NOVA_BORDER, border_width=1,
            text_color=TEXT_PRIMARY, corner_radius=8, height=36,
        )
        self.text_entry.pack(side="left", fill="x", expand=True, padx=10, pady=10)
        self.text_entry.bind("<Return>", self._on_send)

        self.send_button = ctk.CTkButton(
            self.input_frame, text="Send", width=70, height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
            corner_radius=8, command=self._on_send,
        )
        self.send_button.pack(side="right", padx=(0, 10), pady=10)

        self.chat_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.chat_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.after(50, lambda: self._bind_scroll(self.chat_frame))

        # Push-to-talk state — tied to this window's own key events (reliable,
        # no OS-level permissions needed) instead of a global keyboard hook.
        self.space_held = False
        self.space_press_event = threading.Event()
        self.space_release_event = threading.Event()
        self.bind("<KeyPress-space>", self._on_space_press)
        self.bind("<KeyRelease-space>", self._on_space_release)
        self.focus_force()  # make sure this window has keyboard focus on launch

        self.after(100, self.poll_ui_queue)

    def _toggle_window_size(self):
        self.is_compact = not self.is_compact
        if self.is_compact:
            self.geometry(self.COMPACT_SIZE)
            self.bubble_wraplength = self.COMPACT_WRAP
            self.bubble_font_size = self.COMPACT_FONT_SIZE
            self.size_toggle_button.configure(text="⤢ Expand")
        else:
            self.geometry(self.FULL_SIZE)
            self.bubble_wraplength = self.FULL_WRAP
            self.bubble_font_size = self.FULL_FONT_SIZE
            self.size_toggle_button.configure(text="⤡ Compact")
        # Resize existing bubbles (wrap width AND font) to match the new window size
        new_font = ("Segoe UI", self.bubble_font_size)
        for bubble in self.chat_frame.winfo_children():
            bubble.configure(wraplength=self.bubble_wraplength, font=new_font)

    def _on_space_press(self, event):
        # Windows auto-repeats KeyPress while held — only react to the first one
        if not self.space_held:
            self.space_held = True
            self.space_release_event.clear()
            self.space_press_event.set()

    def _on_space_release(self, event):
        self.space_held = False
        self.space_release_event.set()

    def _on_send(self, event=None):
        text = self.text_entry.get().strip()
        if not text:
            return
        self.text_entry.delete(0, "end")
        # Run in a background thread so a slow LLM/command doesn't freeze the window
        threading.Thread(target=text_worker, args=(self, text), daemon=True).start()

    def set_status(self, text: str):
        # Infer a HUD-style state color from the status text so the dot next
        # to it reflects what NOVA's actually doing.
        lowered = text.lower()
        if "speaking" in lowered:
            state = "speaking"
        elif "thinking" in lowered or "transcribing" in lowered:
            state = "thinking"
        elif "hold" in lowered or "talk" in lowered:
            state = "listening"
        elif "offline" in lowered:
            state = "offline"
        else:
            state = "idle"

        def update():
            self.status_label.configure(text=text.upper())
            self.status_dot.itemconfig(self._dot_id, fill=STATUS_COLORS[state])

        self.after(0, update)

    def add_bubble(self, sender: str, text: str):
        is_user = sender == "you"
        bubble = ctk.CTkLabel(
            self.chat_frame,
            text=text,
            wraplength=self.bubble_wraplength,
            justify="left",
            font=("Segoe UI", self.bubble_font_size),
            fg_color=BUBBLE_USER if is_user else BUBBLE_NOVA,
            text_color="white" if is_user else TEXT_PRIMARY,
            corner_radius=14,
            padx=12,
            pady=8,
            **({} if is_user else {"border_width": 1, "border_color": BUBBLE_NOVA_BORDER}),
        )
        bubble.pack(anchor="e" if is_user else "w", pady=4, padx=6)

        # CustomTkinter's scrollable frame only catches scroll/touchpad events
        # over empty space by default — bind them on each bubble too, so
        # scrolling works no matter where the cursor/fingers are over the chat.
        self._bind_scroll(bubble)

        self.chat_frame.update_idletasks()  # force layout to recalculate before scrolling
        self.chat_frame._parent_canvas.yview_moveto(1.0)

    def _bind_scroll(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel)  # Windows/macOS wheel + most touchpads
        widget.bind("<Button-4>", self._on_mousewheel)     # Linux scroll up
        widget.bind("<Button-5>", self._on_mousewheel)     # Linux scroll down

    def _on_mousewheel(self, event):
        canvas = self.chat_frame._parent_canvas
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        else:
            # Precision touchpads send many small delta values (not always
            # multiples of 120 like a physical wheel) — dividing by 120 rounds
            # most of them down to 0, which reads as laggy/unresponsive.
            # Scrolling a fixed step per event, using just the direction, feels
            # smooth on both touchpads and wheels.
            direction = -1 if event.delta > 0 else 1
            canvas.yview_scroll(direction, "units")

    def poll_ui_queue(self):
        try:
            while True:
                sender, text = ui_queue.get_nowait()
                self.add_bubble(sender, text)
        except queue.Empty:
            pass
        self.after(100, self.poll_ui_queue)


def main():
    app = NovaApp()
    threading.Thread(target=worker_loop, args=(app,), daemon=True).start()
    app.mainloop()


if __name__ == "__main__":
    main()
