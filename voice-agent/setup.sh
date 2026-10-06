#!/bin/bash

echo "=================================="
echo "  Voice Agent Setup (Whisper + Coqui TTS)"
echo "=================================="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is not installed. Please install Python 3.8+"
    exit 1
fi

echo "✓ Python3 found: $(python3 --version)"
echo ""

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "✓ Virtual environment created"
echo ""

# Install dependencies
echo "Installing dependencies (this may take 5-10 minutes)..."
echo "Downloading Whisper, Coqui TTS, and PyTorch..."
pip install -r requirements.txt

echo ""
echo "✓ Dependencies installed"
echo ""

# Test imports
echo "Testing imports..."
python3 -c "import whisper; print('✓ Whisper imported successfully')"
python3 -c "from TTS.api import TTS; print('✓ Coqui TTS imported successfully')"
python3 -c "import torch; print(f'✓ PyTorch imported (Device: {torch.device(\"cuda\" if torch.cuda.is_available() else \"cpu\")})')"

echo ""
echo "=================================="
echo "  Setup Complete!"
echo "=================================="
echo ""
echo "To start the voice agent server:"
echo "  cd voice-agent"
echo "  source venv/bin/activate"
echo "  python server.py"
echo ""
echo "The server will start at: http://localhost:8000"
echo ""
echo "First run will download models (~2GB):"
echo "  - Whisper base model (~140MB)"
echo "  - Coqui TTS model (~1.8GB)"
echo ""
