import io
import os
import sys
import tempfile
import asyncio
import base64
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
import whisper
import torch
import edge_tts
from pydantic import BaseModel

# Ensure Whisper can find ffmpeg (used to decode audio)
def _ensure_ffmpeg():
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        bin_dir = os.path.dirname(ffmpeg_exe)
        if bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        print(f"ffmpeg available: {ffmpeg_exe}")
    except ImportError:
        print("imageio-ffmpeg not installed; falling back to system ffmpeg")

_ensure_ffmpeg()

app = FastAPI(title="Voice Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global models
whisper_model = None

# Available voices by language
VOICES = {
    "en": {
        "male": "en-IN-PrabhatNeural",
        "female": "en-IN-NeerjaNeural"
    },
    "hi": {
        "male": "hi-IN-MadhurNeural",
        "female": "hi-IN-SwaraNeural"
    }
}

# Default voices
DEFAULT_VOICES = {
    "en": "en-IN-PrabhatNeural",
    "hi": "hi-IN-MadhurNeural",
    "auto": "en-IN-PrabhatNeural"
}

class VoiceRequest(BaseModel):
    text: str
    language: Optional[str] = "auto"
    voice_gender: Optional[str] = "male"

class VoiceResponse(BaseModel):
    audio_base64: str
    text: str
    language: str

def load_models():
    global whisper_model
    
    print("Loading Whisper model (base)...")
    whisper_model = whisper.load_model("base")
    print("Whisper model loaded!")
    
    print("Edge TTS ready with voices:")
    for lang, voices in VOICES.items():
        print(f"  {lang}: {voices['male']}, {voices['female']}")

@app.on_event("startup")
async def startup_event():
    load_models()

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "whisper_loaded": whisper_model is not None,
        "tts_engine": "edge-tts",
        "supported_languages": list(VOICES.keys()),
        "device": "cuda" if torch.cuda.is_available() else "cpu"
    }

@app.get("/voices")
async def get_voices():
    return {"voices": VOICES}

@app.post("/stt")
async def speech_to_text(audio_data: bytes, language: Optional[str] = "auto"):
    """Convert speech audio to text using Whisper"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name
        
        if language == "auto":
            result = whisper_model.transcribe(tmp_path)
        else:
            result = whisper_model.transcribe(tmp_path, language=language)
        
        os.unlink(tmp_path)
        
        return {
            "text": result["text"],
            "language": result.get("language", "en")
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/tts")
async def text_to_speech(request: VoiceRequest):
    """Convert text to speech using Edge TTS"""
    try:
        # Determine voice based on language and gender
        if request.language in VOICES:
            voice = VOICES[request.language].get(request.voice_gender, DEFAULT_VOICES[request.language])
        else:
            voice = DEFAULT_VOICES["en"]
        
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        
        communicate = edge_tts.Communicate(request.text, voice)
        await communicate.save(tmp_path)
        
        with open(tmp_path, "rb") as f:
            audio_data = f.read()
        
        os.unlink(tmp_path)
        
        audio_base64 = base64.b64encode(audio_data).decode()
        
        return {
            "audio_base64": audio_base64,
            "text": request.text,
            "language": request.language,
            "voice": voice
        }
    except Exception as e:
        return {"error": str(e)}

@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time voice interaction"""
    await websocket.accept()
    
    # Default settings
    language = "auto"
    voice_gender = "male"
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # Update settings if provided
            if "language" in data:
                language = data["language"]
            if "voice_gender" in data:
                voice_gender = data["voice_gender"]
            
            if data.get("type") == "audio":
                audio_bytes = base64.b64decode(data["audio"])
                
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name
                
                # Auto-detect language or use specified
                if language == "auto":
                    result = whisper_model.transcribe(tmp_path)
                else:
                    result = whisper_model.transcribe(tmp_path, language=language)
                
                os.unlink(tmp_path)
                
                user_text = result["text"]
                detected_lang = result.get("language", "en")
                
                await websocket.send_json({
                    "type": "transcript",
                    "text": user_text,
                    "language": detected_lang
                })
                
                # Generate response in detected language
                agent_response = generate_response(user_text, data.get("context", {}), detected_lang)
                
                # Use voice matching detected language
                tts_lang = detected_lang if detected_lang in VOICES else "en"
                voice = VOICES[tts_lang].get(voice_gender, DEFAULT_VOICES[tts_lang])
                
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tts_path = tmp.name
                
                communicate = edge_tts.Communicate(agent_response, voice)
                await communicate.save(tts_path)
                
                with open(tts_path, "rb") as f:
                    tts_audio = f.read()
                
                os.unlink(tts_path)
                
                await websocket.send_json({
                    "type": "audio_response",
                    "audio_base64": base64.b64encode(tts_audio).decode(),
                    "text": agent_response,
                    "language": tts_lang,
                    "voice": voice
                })
            
            elif data.get("type") == "text":
                user_text = data.get("text", "")
                
                # Detect language from text
                detected_lang = detect_language(user_text)
                
                agent_response = generate_response(user_text, data.get("context", {}), detected_lang)
                
                # Use voice matching detected language
                tts_lang = detected_lang if detected_lang in VOICES else "en"
                voice = VOICES[tts_lang].get(voice_gender, DEFAULT_VOICES[tts_lang])
                
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tts_path = tmp.name
                
                communicate = edge_tts.Communicate(agent_response, voice)
                await communicate.save(tts_path)
                
                with open(tts_path, "rb") as f:
                    tts_audio = f.read()
                
                os.unlink(tts_path)
                
                await websocket.send_json({
                    "type": "audio_response",
                    "audio_base64": base64.b64encode(tts_audio).decode(),
                    "text": agent_response,
                    "language": tts_lang,
                    "voice": voice
                })
    
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"WebSocket error: {e}")
        await websocket.close()

def detect_language(text: str) -> str:
    """Simple language detection based on character patterns"""
    # Check for Devanagari script (Hindi)
    if any('\u0900' <= c <= '\u097F' for c in text):
        return "hi"
    
    # Check for common Hindi words in Roman script (Hinglish)
    hindi_words = ['hai', 'hain', 'kya', 'nahi', 'haan', 'acha', 'theek', 'samajh', 'bolo', 'suno']
    text_lower = text.lower()
    if any(word in text_lower for word in hindi_words):
        return "hi"
    
    return "en"

def generate_response(user_text: str, context: dict, language: str = "en") -> str:
    """Generate agent response based on user input and language"""
    product_name = context.get("product_name", "this product")
    current_price = context.get("current_price", 0)
    mrp = context.get("mrp", 0)
    min_price = context.get("min_price", 0)
    
    lower_text = user_text.lower()
    
    import re
    price_match = None
    numbers = re.findall(r'\d+', user_text)
    if numbers:
        price_match = int(numbers[0])
    
    # Hindi responses
    if language == "hi":
        if "final" in lower_text or "aakhri" in lower_text or "last" in lower_text:
            return f"Meri aakhri offer {current_price} rupaye hai. Bahut acchi deal hai!"
        
        if "bye" in lower_text or "band" in lower_text or "khatam" in lower_text:
            return f"Aapka samay ke liye dhanyavaad! {current_price} rupaye ki offer hai. Kabhi bhi aaiye!"
        
        if price_match is not None:
            if price_match >= current_price:
                return f"Bahut accha! {price_match} rupaye mere liye thik hai. Chalo checkout karte hain!"
            
            if price_match < min_price:
                counter_offer = (min_price + current_price) // 2
                return f"Mujhe khushi hai, lekin {price_match} rupaye bahut kam hai. Kya {counter_offer} rupaye ho sakta hai?"
            
            if min_price <= price_match < current_price:
                new_price = (price_match + current_price) // 2
                return f"Kya {new_price} rupaye ho sakta hai? Hum paas aa rahe hain!"
        
        responses = [
            f"Is waqt price {current_price} rupaye hai. Kya aap offer karna chahenge?",
            f"Main aapko {product_name} ke liye {current_price} rupaye de sakta hoon. Aapko kya lagta hai?",
            f"Price {current_price} rupaye hai. Kya hum finalize kar dein?",
        ]
        
        import random
        return random.choice(responses)
    
    # English responses (default)
    if "final" in lower_text or "best price" in lower_text:
        return f"My final offer is {current_price} rupees. That's a great deal!"
    
    if "bye" in lower_text or "close" in lower_text or "end" in lower_text:
        return f"Thank you for your time! The offer of {current_price} rupees stands. Come back anytime!"
    
    if price_match is not None:
        if price_match >= current_price:
            return f"Great! {price_match} rupees works for me. Let's proceed with the checkout!"
        
        if price_match < min_price:
            counter_offer = (min_price + current_price) // 2
            return f"I'm sorry, {price_match} rupees is too low. How about {counter_offer} rupees?"
        
        if min_price <= price_match < current_price:
            new_price = (price_match + current_price) // 2
            return f"How about {new_price} rupees? We're getting closer!"
    
    responses = [
        f"The current price is {current_price} rupees. Would you like to make an offer?",
        f"I can offer you {current_price} rupees for {product_name}. What do you think?",
        f"The price is {current_price} rupees. Shall we finalize?",
    ]
    
    import random
    return random.choice(responses)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
