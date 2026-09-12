# ULTRON — Jarvis-Style AI Desktop Assistant

A **JARVIS-inspired desktop AI assistant** built in Python, with the name **ULTRON**.

ULTRON combines voice recognition, wake-word interaction, Gemini-powered conversation, persistent memory, Windows controls, text-to-speech, and a futuristic holographic HUD into one desktop application.

> **Project status:** Personal/experimental project. Features and behavior may change as the assistant evolves.

## ✨ Features

### 🎙️ Voice Assistant
- Wake-word support using **“Ultron”**
- Natural voice-command capture with silence detection
- Speech recognition powered by **faster-whisper**
- CPU INT8 mode by default for easier Windows setup
- Optional CUDA/GPU mode through an environment variable
- Text-to-speech using **Edge TTS**
- Optional robotic voice filter

### 🧠 AI Conversation
- Gemini-powered responses
- Short, voice-friendly answers
- Recent conversation context
- Persistent local memory
- Memory-aware responses

### 💾 Persistent Memory
ULTRON stores memories locally in:

```text
ultron_memory.db
```

Example commands:

```text
Remember that my favorite color is purple.
My name is Prajwal.
What do you remember?
What's my name?
Forget that my favorite color is purple.
Forget everything.
```

The memory database is created next to the Python script.

### 🖥️ Windows Controls
ULTRON can perform common desktop tasks locally, including:

```text
Open Chrome
Close Spotify
Take a screenshot
Show desktop
Lock computer
Open Downloads
Open Documents
Open Pictures
List running apps
Volume up
Volume down
Mute
Play / pause
Next song
Previous song
```

Power actions such as shutdown and restart require confirmation.

### 📸 Screenshots
Say:

```text
Take a screenshot
```

Screenshots are saved to:

```text
Pictures\Ultron Screenshots
```

### 🌐 Web Search
You can ask:

```text
Search web for <query>
```

ULTRON opens the search in your browser.

### 🖥️ Holographic HUD
The interface includes:
- Animated central neural/core visualization
- Signal monitor
- System telemetry
- Diagnostics
- Communication console
- Wake-link controls
- Voice settings
- Futuristic purple/cyan visual design

## 🧩 Architecture

```text
Microphone
    ↓
Wake-word detection
    ↓
faster-whisper
    ↓
Command / intent handling
    ├── Windows controls
    ├── Screenshot
    ├── Memory
    ├── Web browser
    └── Gemini
            ↓
        Response
            ↓
       Edge TTS
```

ULTRON handles simple computer actions locally instead of sending every command to Gemini.

## 📦 Requirements

Recommended:
- Windows 10/11
- Python 3.10+
- Working microphone
- Internet connection for Gemini / Edge TTS and initial Whisper model download
- A Gemini API key

Install the main dependencies:

```powershell
pip install faster-whisper sounddevice numpy soundfile edge-tts playsound customtkinter python-dotenv google-genai pystray Pillow
```

## 🔑 Gemini API Key

Create a `.env` file in the same folder as the Python script:

```env
GEMINI_API_KEY=your_api_key_here
```

The application reads the key from `.env`.

**Do not commit your `.env` file to GitHub.**

Recommended `.gitignore` entries:

```gitignore
.env
ultron_memory.db
__pycache__/
*.pyc
```

## 🎧 Whisper Configuration

Default model:

```env
WHISPER_MODEL=base.en
```

### CPU

CPU INT8 is the default:

```env
WHISPER_DEVICE=cpu
```

### NVIDIA GPU

GPU mode can be enabled with:

```env
WHISPER_DEVICE=cuda
```

Your computer must have the required CUDA/cuDNN runtime installed and working for this mode.

Optional decoding setting:

```env
WHISPER_BEAM_SIZE=3
```

## ▶️ Running ULTRON

Open PowerShell in the project directory:

```powershell
cd "C:\path\to\UltronV2"
```

Run your chosen Python file, for example:

```powershell
python ultron_gui.py
```

or:

```powershell
python ultron_jarvis_style_v4.py
```

The first run can take longer because the Whisper model may need to be downloaded.

## 🗣️ Example Commands

### Computer

```text
Ultron, open Chrome.
Ultron, close Spotify.
Ultron, take a screenshot.
Ultron, show desktop.
Ultron, lock my computer.
```

### Media

```text
Ultron, turn the volume up.
Ultron, mute.
Ultron, next song.
Ultron, pause.
```

### Memory

```text
Ultron, remember that my favorite color is purple.
Ultron, my name is Prajwal.
Ultron, what's my name?
Ultron, what do you remember?
```

### AI

```text
Ultron, explain quantum computing.
Ultron, help me plan my day.
Ultron, search web for the latest Python release.
```

## ⚠️ Safety

Some commands can affect the computer directly.

Power actions such as shutdown and restart require confirmation.

Be careful when expanding the command system to include:
- File deletion
- Bulk file operations
- Application termination
- System configuration
- Network or security controls

Only add automation you understand and trust.

## 📁 Project Structure

A typical project folder can look like:

```text
UltronV2/
├── ultron_gui.py
├── .env
├── ultron_memory.db
├── README.md
└── ...
```

## 🚀 Future Ideas

Possible future improvements:
- Better dedicated wake-word detection
- Streaming Whisper transcription
- More natural interruption handling
- Multi-step actions
- More Windows automation
- Calendar and reminders
- Smart home integration
- Plugin/skill architecture
- Better long-term memory retrieval
- Animated voice-reactive HUD
- Custom ULTRON voice effects

## 🛠️ Contributing

This is currently a personal project, but improvements are welcome.

Good contributions should:
1. Keep commands predictable and safe.
2. Avoid exposing API keys or personal data.
3. Keep voice interaction responsive.
4. Preserve the ULTRON identity and UI style.

## 📜 License

Choose a license before publishing the repository publicly.

Common choices include:
- MIT
- Apache-2.0
- GPL-3.0

Do not add a license you do not intend to use.

---

## ULTRON

**Voice. Memory. Computer control. AI.**

Built as a personal experiment to create a JARVIS-style desktop assistant — with **ULTRON** as the identity.
