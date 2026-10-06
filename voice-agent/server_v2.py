"""
Voice Agent v2 — Real-time AI conversation with Ollama LLM + Edge TTS + Whisper STT
Flow: Speech → Whisper STT → Ollama LLM (streaming) → Edge TTS → Audio response
"""
import io, os, sys, json, base64, asyncio, tempfile, time, re
import struct, wave, threading, numpy as np
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Ensure whisper can find ffmpeg (from imageio-ffmpeg)
def _setup_ffmpeg():
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        bin_dir = os.path.dirname(ffmpeg_exe)
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        # Also create a symlink named 'ffmpeg' if not exists
        link = os.path.join(bin_dir, "ffmpeg")
        if not os.path.exists(link):
            os.symlink(ffmpeg_exe, link)
        print(f"ffmpeg: {ffmpeg_exe}", flush=True)
    except ImportError:
        print("WARNING: imageio-ffmpeg not installed", flush=True)

_setup_ffmpeg()

import whisper
import edge_tts
import ollama

app = FastAPI(title="Voice Agent v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── Config ──────────────────────────────────────────────────────────
WHISPER_MODEL = "base"
OLLAMA_MODEL = "qwen2.5:7b"
TTS_VOICES = {
    "en": {"m": "en-IN-PrabhatNeural", "f": "en-IN-NeerjaNeural"},
    "hi": {"m": "hi-IN-MadhurNeural", "f": "hi-IN-SwaraNeural"},
}
# Silence detection
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 500
SILENCE_DURATION_MS = 800
CHUNK_MS = 100

SYSTEM_PROMPT = """You are an empathetic, intelligent, and warm AI bargaining assistant for Gajab.com — an Indian e-commerce platform.

RULES:
1. ALWAYS respond in the SAME LANGUAGE the customer speaks (English, Hindi, or Hinglish)
2. Keep responses SHORT (1-2 sentences max — this is voice, not text)
3. Be warm, natural, and human — like a friendly shopkeeper, NOT a robot
4. Acknowledge the customer's feelings before negotiating
5. You have authority to negotiate down from current_price to min_price
6. NEVER go below min_price — that's your floor
7. Use Indian cultural warmth: "Arre!", "Bilkul!", "Zaroor!", "Accha ji!"
8. Mirror the customer's energy — if they're casual, be casual; if formal, be formal
9. When they make a reasonable offer, accept warmly and guide to checkout
10. If they push too low, counter with empathy: "Samajh sakta hoon, lekin itna neeche nahi ja sakta"

PERSONALITY:
- You genuinely want to help the customer get a good deal
- You're patient and understanding
- You make the customer feel heard and valued
- You use natural pauses and conversational tone"""


# ── Models ──────────────────────────────────────────────────────────
whisper_model = None

def load_models():
    global whisper_model
    print("Loading Whisper (base)...", flush=True)
    whisper_model = whisper.load_model(WHISPER_MODEL)
    print("Whisper loaded!", flush=True)
    # Warm up Ollama
    print("Warming up Ollama...", flush=True)
    t0 = time.time()
    list(ollama.chat(model=OLLAMA_MODEL, messages=[{"role":"user","content":"hi"}], stream=True))
    print(f"Ollama ready ({time.time()-t0:.1f}s)", flush=True)

@app.on_event("startup")
async def startup():
    load_models()

@app.get("/health")
async def health():
    return {"status": "ok", "whisper": WHISPER_MODEL, "llm": OLLAMA_MODEL, "tts": "edge-tts"}

# ── STT ─────────────────────────────────────────────────────────────
def transcribe_audio(audio_np: np.ndarray, language: str = None) -> dict:
    """Transcribe float32 numpy array (16kHz) → {text, language}"""
    t0 = time.time()
    opts = {}
    if language and language != "auto":
        opts["language"] = language
    opts["fp16"] = False
    result = whisper_model.transcribe(audio_np, **opts)
    elapsed = time.time() - t0
    text = result["text"].strip()
    lang = result.get("language", "en")
    print(f"  STT ({elapsed:.1f}s): [{lang}] {text}", flush=True)
    return {"text": text, "language": lang, "time": elapsed}

# ── LLM ─────────────────────────────────────────────────────────────
def detect_lang_from_text(text: str) -> str:
    if any('\u0900' <= c <= '\u097F' for c in text):
        return "hi"
    hinglish = ['hai','hain','kya','nahi','haan','acha','theek','samajh','bolo','suno','main','aap','yeh','woh','kitna','de','lo','kar']
    low = text.lower()
    if sum(1 for w in hinglish if w in low) >= 2:
        return "hi"
    return "en"

async def stream_llm_response(user_text: str, context: dict, language: str) -> dict:
    """Stream response from Ollama, return {text, chunks}"""
    product = context.get("product_name", "this product")
    price = context.get("current_price", 0)
    mrp = context.get("mrp", 0)
    min_price = context.get("min_price", 0)

    ctx_msg = (
        f"Context: The customer is bargaining for '{product}'. "
        f"Current price is ₹{price}, MRP is ₹{mrp}, minimum you can accept is ₹{min_price}. "
        f"Respond in {'Hindi or Hinglish' if language == 'hi' else 'English'}. "
        f"Keep it to 1-2 short sentences. Be warm and natural."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + ctx_msg},
        {"role": "user", "content": user_text},
    ]

    t0 = time.time()
    chunks = []
    full_text = ""
    for chunk in ollama.chat(model=OLLAMA_MODEL, messages=messages, stream=True):
        token = chunk["message"]["content"]
        chunks.append(token)
        full_text += token
        yield token  # Stream tokens to caller

    elapsed = time.time() - t0
    print(f"  LLM ({elapsed:.1f}s): {full_text[:100]}", flush=True)
    # Final yield with metadata
    yield {"_done": True, "text": full_text, "time": elapsed, "chunks": chunks}

# ── TTS ─────────────────────────────────────────────────────────────
async def tts_generate(text: str, language: str = "en", voice_gender: str = "m") -> bytes:
    """Generate MP3 audio from text using Edge TTS"""
    lang = language if language in TTS_VOICES else "en"
    voice = TTS_VOICES[lang].get(voice_gender, TTS_VOICES[lang]["m"])
    communicate = edge_tts.Communicate(text, voice)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks)


# ── WebSocket ───────────────────────────────────────────────────────
@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()
    print("[WS] Client connected", flush=True)

    language = "auto"
    voice_gender = "m"
    context = {}
    audio_buffer = bytearray()
    is_processing = False
    stop_event = threading.Event()

    def send_msg(data):
        asyncio.run_coroutine_threadsafe(
            websocket.send_json(data), websocket.app.router
        ) if False else None

    try:
        while True:
            msg = await websocket.receive()

            # Handle text (JSON control messages)
            if "text" in msg:
                data = json.loads(msg["text"])
                msg_type = data.get("type", "")

                if msg_type == "settings":
                    language = data.get("language", "auto")
                    voice_gender = data.get("voice_gender", "m")
                    print(f"[WS] Settings: lang={language}, voice={voice_gender}", flush=True)

                elif msg_type == "context":
                    context = data.get("context", {})
                    print(f"[WS] Context set: {context.get('product_name','?')}", flush=True)

                elif msg_type == "audio":
                    # Decode base64 audio → process
                    audio_b64 = data["audio"]
                    context = data.get("context", context)

                    audio_bytes = base64.b64decode(audio_b64)

                    # Write to temp WAV (MediaRecorder outputs webm/opus, whisper reads via ffmpeg)
                    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                        tmp.write(audio_bytes)
                        tmp_path = tmp.name

                    try:
                        audio_np = whisper.load_audio(tmp_path).astype(np.float32)
                        os.unlink(tmp_path)
                    except Exception as e:
                        print(f"  Audio load error: {e}", flush=True)
                        try: os.unlink(tmp_path)
                        except: pass
                        continue

                    if len(audio_np) < SAMPLE_RATE * 0.3:
                        continue  # Too short

                    # ── Pipeline: STT → LLM → TTS ──
                    await websocket.send_json({"type": "status", "status": "thinking"})

                    # 1. Transcribe
                    stt_result = transcribe_audio(audio_np, language)
                    if not stt_result["text"]:
                        continue

                    # Detect language
                    detected_lang = stt_result["language"]
                    if language == "auto":
                        text_lang = detect_lang_from_text(stt_result["text"])
                        detected_lang = text_lang if text_lang else detected_lang

                    await websocket.send_json({
                        "type": "transcript",
                        "text": stt_result["text"],
                        "language": detected_lang,
                    })

                    # 2. LLM (streaming)
                    await websocket.send_json({"type": "status", "status": "thinking"})
                    llm_result = ""
                    llm_meta = {}
                    async for token in stream_llm_response(stt_result["text"], context, detected_lang):
                        if isinstance(token, dict) and token.get("_done"):
                            llm_result = token["text"]
                            llm_meta = token
                        else:
                            # Send streaming token for UI
                            await websocket.send_json({
                                "type": "llm_stream", "token": token
                            })

                    if not llm_result:
                        continue

                    # 3. TTS
                    await websocket.send_json({"type": "status", "status": "speaking"})
                    tts_lang = detected_lang if detected_lang in TTS_VOICES else "en"
                    tts_audio = await tts_generate(llm_result, tts_lang, voice_gender)

                    await websocket.send_json({
                        "type": "audio_response",
                        "audio_base64": base64.b64encode(tts_audio).decode(),
                        "text": llm_result,
                        "language": tts_lang,
                    })

                    await websocket.send_json({"type": "status", "status": "listening"})

                elif msg_type == "text":
                    # Direct text input (for testing / chat mode)
                    user_text = data.get("text", "")
                    context = data.get("context", context)

                    detected_lang = detect_lang_from_text(user_text)

                    await websocket.send_json({"type": "status", "status": "thinking"})

                    # LLM streaming
                    llm_result = ""
                    async for token in stream_llm_response(user_text, context, detected_lang):
                        if isinstance(token, dict) and token.get("_done"):
                            llm_result = token["text"]
                        else:
                            await websocket.send_json({"type": "llm_stream", "token": token})

                    if not llm_result:
                        continue

                    # TTS
                    await websocket.send_json({"type": "status", "status": "speaking"})
                    tts_lang = detected_lang if detected_lang in TTS_VOICES else "en"
                    tts_audio = await tts_generate(llm_result, tts_lang, voice_gender)

                    await websocket.send_json({
                        "type": "audio_response",
                        "audio_base64": base64.b64encode(tts_audio).decode(),
                        "text": llm_result,
                        "language": tts_lang,
                    })

                    await websocket.send_json({"type": "status", "status": "listening"})

                elif msg_type == "interrupt":
                    # Client wants to stop current audio
                    await websocket.send_json({"type": "status", "status": "listening"})

    except WebSocketDisconnect:
        print("[WS] Client disconnected", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[WS] Error: {e}", flush=True)
        try: await websocket.close()
        except: pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
