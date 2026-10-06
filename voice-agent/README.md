# Voice Agent - Whisper + Coqui TTS

100% offline voice agent for product bargaining. Runs locally on your machine.

## Architecture

```
Frontend (React) ←→ WebSocket ←→ Python Backend (FastAPI)
                                      ↓
                              ┌───────┴───────┐
                              │               │
                         Whisper STT    Coqui TTS
                         (Speech→Text)  (Text→Speech)
```

## Setup

### 1. Install Python Dependencies

```bash
cd voice-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the Server

```bash
cd voice-agent
source venv/bin/activate
python start.py
```

First run will download models (~2GB):
- Whisper base model (~140MB)
- Coqui TTS model (~1.8GB)

### 3. Open the App

1. Go to http://localhost:5173
2. Click "Product Enhancement"
3. Select a product
4. Click "Start Bargaining"
5. Click "Start Voice Bargain"

## API Endpoints

- `GET /health` - Check server status
- `POST /stt` - Speech to text
- `POST /tts` - Text to speech
- `WebSocket /ws/voice` - Real-time voice interaction

## Features

- **100% Offline** - No data leaves your machine
- **Whisper STT** - OpenAI's speech recognition
- **Coqui TTS** - High-quality text-to-speech
- **Real-time** - WebSocket for instant responses
- **Voice + Text** - Type or speak your offers

## System Requirements

- Python 3.8+
- 4GB RAM minimum
- 2GB disk space for models
- Microphone access in browser

## Troubleshooting

### Server won't start
```bash
# Make sure you're in the virtual environment
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

### No microphone access
- Allow microphone access in browser settings
- Use Chrome, Edge, or Safari

### Audio not playing
- Check browser console for errors
- Ensure server is running on port 8000
