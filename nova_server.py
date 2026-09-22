"""
N.O.V.A. Mobile — access NOVA from your phone's browser over the same
local network (e.g. your phone's own hotspot, which your laptop is on).

Your laptop still does all the real thinking (Ollama). Your phone's
browser handles the mic and voice output, using its own built-in speech
recognition and text-to-speech — no extra apps needed on the phone.

SETUP:
    pip install flask

    Run this on your LAPTOP:
        python nova_server.py

    Find your laptop's local IP:
        ipconfig   (look for "IPv4 Address" under your WiFi adapter —
                     the one connected to your phone's hotspot)

    On your PHONE's browser, go to:
        http://<that IP>:5000

    Use Chrome on the phone — it has the best support for browser-based
    voice input (Web Speech API).
"""

import json
import os
import requests
from flask import Flask, request, jsonify, Response
from datetime import datetime
import subprocess
import webbrowser

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5:7b-instruct-q4_K_M"
HISTORY_FILE = "nova_mobile_history.json"  # separate from the desktop app's history
MAX_HISTORY_MESSAGES = 40

SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are N.O.V.A. (Nocturnal Offline Voice Anomaly), a personal voice "
        "assistant. Keep answers short and conversational, since your replies "
        "are spoken aloud."
    ),
}


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and saved[0].get("role") == "system":
                saved[0] = SYSTEM_PROMPT
            else:
                saved.insert(0, SYSTEM_PROMPT)
            return saved
        except Exception:
            pass
    return [SYSTEM_PROMPT]


def save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(chat_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] couldn't save history: {e}")


chat_history = load_history()


# ---------------- COMMANDS (same set as the desktop app) ----------------
def cmd_time(text): return f"It's {datetime.now().strftime('%I:%M %p')}."
def cmd_date(text): return f"Today is {datetime.now().strftime('%A, %B %d')}."


def cmd_weather(text):
    city = text.split("in", 1)[1].strip() if " in " in text else ""
    url = f"https://wttr.in/{city}?format=%C+%t" if city else "https://wttr.in/?format=%C+%t"
    try:
        resp = requests.get(url, timeout=10)
        return f"It's currently {resp.text.strip()}" + (f" in {city}." if city else ".")
    except Exception:
        return "Couldn't fetch the weather right now."


def cmd_search(text):
    for trigger in ("search for", "search", "google"):
        if trigger in text:
            query = text.split(trigger, 1)[1].strip()
            break
    else:
        query = text
    # Opens on the LAPTOP, not the phone, since that's where this server runs
    webbrowser.open(f"https://www.google.com/search?q={query}")
    return f"Searching for {query} on your laptop."


COMMANDS = [
    (["what time is it", "what's the time", "what is the time", "tell me the time", "current time", "time is it"], cmd_time),
    (["what's the date", "what is the date", "tell me the date", "today's date", "what day is it"], cmd_date),
    (["weather"], cmd_weather),
    (["search for", "search ", "google "], cmd_search),
]


def handle_command(text):
    lowered = text.lower()
    for phrases, handler in COMMANDS:
        if any(phrase in lowered for phrase in phrases):
            return handler(lowered)
    return None


def ask_nova(user_text: str) -> str:
    chat_history.append({"role": "user", "content": user_text})
    try:
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
        return "I can't reach my brain right now. Is Ollama running on your laptop?"
    except Exception as e:
        return f"Something went wrong: {e}"


# ---------------- WEB SERVER ----------------
app = Flask(__name__)

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>N.O.V.A.</title>
<style>
  :root {
    --bg-primary: #0A0E17;
    --bg-panel: #10141F;
    --bubble-nova: #1B2233;
    --bubble-nova-border: #3A3F63;
    --bubble-user: #6C5CE7;
    --text-primary: #E8EAF0;
    --text-dim: #7A8099;
    --accent: #7C5CFC;
    --accent-hover: #6A4CE0;
    --status-idle: #5A6079;
    --status-listening: #7C5CFC;
    --status-thinking: #E0A64B;
    --status-speaking: #3FD6B5;
  }
  * { box-sizing: border-box; }
  html, body {
    height: 100%; margin: 0; background: var(--bg-primary); color: var(--text-primary);
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
  #app { display: flex; flex-direction: column; height: 100%; }
  #topbar {
    display: flex; align-items: center; gap: 8px;
    background: var(--bg-panel); padding: 12px 16px; flex-shrink: 0;
  }
  #dot { width: 10px; height: 10px; border-radius: 50%; background: var(--status-idle); transition: background 0.3s; }
  #status { font-family: Consolas, monospace; font-size: 12px; color: var(--text-dim); text-transform: uppercase; flex: 1; }
  #chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
  .bubble { max-width: 78%; padding: 10px 14px; border-radius: 16px; font-size: 15px; line-height: 1.4; white-space: pre-wrap; }
  .nova { align-self: flex-start; background: var(--bubble-nova); border: 1px solid var(--bubble-nova-border); }
  .user { align-self: flex-end; background: var(--bubble-user); color: white; }
  #inputrow { display: flex; gap: 8px; padding: 12px; background: var(--bg-panel); flex-shrink: 0; }
  #textbox {
    flex: 1; border-radius: 8px; border: 1px solid var(--bubble-nova-border);
    background: var(--bg-primary); color: var(--text-primary); padding: 10px 12px; font-size: 15px;
  }
  button {
    border: none; border-radius: 8px; padding: 10px 16px; font-size: 15px;
    background: var(--accent); color: white; cursor: pointer;
  }
  button:active { background: var(--accent-hover); }
  #micbtn.listening { background: var(--status-listening); animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
</head>
<body>
<div id="app">
  <div id="topbar"><div id="dot"></div><div id="status">READY</div></div>
  <div id="chat"></div>
  <div id="inputrow">
    <button id="micbtn">🎤</button>
    <input id="textbox" placeholder="Type or hold mic to talk..." />
    <button id="sendbtn">Send</button>
  </div>
</div>
<script>
const chat = document.getElementById('chat');
const statusEl = document.getElementById('status');
const dot = document.getElementById('dot');
const textbox = document.getElementById('textbox');
const micbtn = document.getElementById('micbtn');

function setStatus(text, state) {
  statusEl.textContent = text.toUpperCase();
  const colors = {idle:'var(--status-idle)', listening:'var(--status-listening)',
                   thinking:'var(--status-thinking)', speaking:'var(--status-speaking)'};
  dot.style.background = colors[state] || colors.idle;
}

function addBubble(sender, text) {
  const b = document.createElement('div');
  b.className = 'bubble ' + (sender === 'you' ? 'user' : 'nova');
  b.textContent = text;
  chat.appendChild(b);
  chat.scrollTop = chat.scrollHeight;
}

function speak(text) {
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.0;
  setStatus('Speaking...', 'speaking');
  utter.onend = () => setStatus('Ready', 'idle');
  speechSynthesis.speak(utter);
}

async function sendMessage(text) {
  if (!text.trim()) return;
  addBubble('you', text);
  textbox.value = '';
  setStatus('Thinking...', 'thinking');
  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const data = await res.json();
    addBubble('nova', data.reply);
    speak(data.reply);
  } catch (e) {
    addBubble('nova', "Couldn't reach the server.");
    setStatus('Ready', 'idle');
  }
}

document.getElementById('sendbtn').onclick = () => sendMessage(textbox.value);
textbox.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMessage(textbox.value); });

// Voice input via the phone browser's built-in speech recognition
let recognition = null;
let listening = false;
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.interimResults = false;

  recognition.onresult = (event) => {
    const text = event.results[0][0].transcript;
    sendMessage(text);
  };
  recognition.onend = () => {
    listening = false;
    micbtn.classList.remove('listening');
    setStatus('Ready', 'idle');
  };

  micbtn.addEventListener('click', () => {
    if (listening) {
      recognition.stop();
    } else {
      listening = true;
      micbtn.classList.add('listening');
      setStatus('Listening...', 'listening');
      recognition.start();
    }
  });
} else {
  micbtn.disabled = true;
  micbtn.title = "Voice input not supported in this browser — try Chrome";
}

// Load past conversation on page load
fetch('/api/history').then(r => r.json()).then(data => {
  data.forEach(m => addBubble(m.sender, m.text));
});
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    text = request.json.get("message", "").strip()
    if not text:
        return jsonify({"reply": ""})

    command_reply = handle_command(text)
    if command_reply is not None:
        chat_history.append({"role": "user", "content": text})
        chat_history.append({"role": "assistant", "content": command_reply})
        save_history()
        reply = command_reply
    else:
        reply = ask_nova(text)

    return jsonify({"reply": reply})


@app.route("/api/history")
def api_history():
    out = []
    for msg in chat_history[1:]:  # skip system prompt
        sender = "you" if msg["role"] == "user" else "nova"
        out.append({"sender": sender, "text": msg["content"]})
    return jsonify(out)


if __name__ == "__main__":
    print("N.O.V.A. Mobile server starting...")
    print("Find your laptop's local IP with 'ipconfig', then on your phone go to:")
    print("  http://<that-ip>:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
