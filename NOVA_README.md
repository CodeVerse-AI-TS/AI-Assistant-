# N.O.V.A. — Nocturnal Offline Voice Anomaly

A fully local, offline push-to-talk voice assistant. No cloud APIs, no API keys, no internet required after setup — speech recognition, the language model, and text-to-speech all run on your own machine.

## Pipeline

Hold **SPACE** (while the NOVA window is focused) → speak → release → **Whisper** (faster-whisper, GPU-accelerated) transcribes → known commands are handled instantly, otherwise **Ollama** (local LLM) generates a reply → **edge-tts** speaks it back. Conversation history is saved locally and reloaded on every restart.

## Features

- 100% offline inference (speech-to-text, LLM, and text-to-speech all run locally)
- GPU-accelerated transcription via faster-whisper + CUDA
- Built-in voice commands: time, date, weather, opening apps, web search, volume control, lock/shutdown PC
- Persistent conversation memory across restarts
- Simple chat window UI (CustomTkinter)

## Setup

1. Install [Ollama](https://ollama.com) and pull a model:
   ```
   ollama pull qwen2.5:7b-instruct-q4_K_M
   ```

2. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

3. (Windows + NVIDIA GPU only) If you hit a `cublas64_12.dll not found` error, copy the CUDA DLLs into the ctranslate2 package folder:
   ```
   copy "<python_path>\Lib\site-packages\nvidia\cublas\bin\*.dll" "<python_path>\Lib\site-packages\ctranslate2\"
   copy "<python_path>\Lib\site-packages\nvidia\cudnn\bin\*.dll" "<python_path>\Lib\site-packages\ctranslate2\"
   ```

4. Run it:
   ```
   python nova.py
   ```

## Configuration

Edit the `CONFIG` section at the top of `nova.py`:
- `WHISPER_MODEL_SIZE` — tiny / base / small / medium (bigger = more accurate, slower)
- `TTS_VOICE` — any edge-tts voice name
- `APP_PATHS` — apps NOVA can open by voice command

## Notes

- `nova_history.json` (your saved conversations) is gitignored — it's personal and stays local.
- Run as a Windows startup app by placing a `.bat` launcher in `shell:startup`.
