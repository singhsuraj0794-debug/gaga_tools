#!/usr/bin/env python3
"""
Voice Agent Server - Whisper + Coqui TTS
100% Offline Voice Agent for Bargaining
"""

import os
import sys
import asyncio
from pathlib import Path

def check_requirements():
    """Check if all requirements are installed"""
    try:
        import whisper
        print("✓ Whisper installed")
    except ImportError:
        print("✗ Whisper not installed. Run: pip install openai-whisper")
        return False
    
    try:
        from TTS.api import TTS
        print("✓ Coqui TTS installed")
    except ImportError:
        print("✗ Coqui TTS not installed. Run: pip install TTS")
        return False
    
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"✓ PyTorch installed (Device: {device})")
    except ImportError:
        print("✗ PyTorch not installed. Run: pip install torch")
        return False
    
    return True

def main():
    print("=" * 50)
    print("  Voice Agent Server (Whisper + Coqui TTS)")
    print("=" * 50)
    print()
    
    # Check requirements
    print("Checking requirements...")
    if not check_requirements():
        print()
        print("Please install missing requirements:")
        print("  pip install -r requirements.txt")
        sys.exit(1)
    
    print()
    print("All requirements satisfied!")
    print()
    
    # Start server
    print("Starting voice agent server on port 8000...")
    print("First run will download models (~2GB)...")
    print()
    print("Server URL: http://localhost:8000")
    print("WebSocket URL: ws://localhost:8000/ws/voice")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 50)
    print()
    
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
