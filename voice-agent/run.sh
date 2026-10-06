#!/bin/bash

echo "=================================="
echo "  Voice Agent Server"
echo "  Whisper + Edge TTS"
echo "=================================="
echo ""

cd "$(dirname "$0")"

source venv/bin/activate

echo "Starting voice agent server..."
echo "URL: http://localhost:8000"
echo "WebSocket: ws://localhost:8000/ws/voice"
echo ""
echo "Press Ctrl+C to stop"
echo "=================================="
echo ""

python server.py
