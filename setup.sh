#!/bin/bash
# Finch — One-command setup
# Run: chmod +x setup.sh && ./setup.sh

set -e

echo "========================================="
echo "  Finch — Autonomous AI Co-Founder Setup"
echo "========================================="
echo ""

# Check Python
echo "[1/6] Checking Python..."
python3 --version || { echo "Python 3 required."; exit 1; }

# Create venv
echo "[2/6] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "[3/6] Installing core dependencies..."
pip install --upgrade pip
pip install chromadb ollama pyyaml apscheduler requests beautifulsoup4 pandas numpy rich pydub python-telegram-bot scikit-learn

# Install Ollama if not present
echo "[4/6] Checking Ollama..."
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Pull LLM
echo "[5/6] Pulling LLM model (llama3:8b)..."
ollama pull llama3:8b

# Create data directories
echo "[6/6] Creating data directories..."
mkdir -p data/memory data/leads data/clients data/audio

# Optional: install voice dependencies
echo ""
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "To install optional local voice support:"
echo "  source venv/bin/activate"
echo "  pip install openai-whisper piper-tts pyaudio SpeechRecognition"
echo ""
echo "To start Finch:"
echo "  source venv/bin/activate"
echo "  python main.py"
echo ""
echo "To run as background daemon:"
echo "  sudo cp finch.service /etc/systemd/system/"
echo "  sudo systemctl enable finch"
echo "  sudo systemctl start finch"
echo ""
echo "Set environment variables for full functionality:"
echo "  export FINCH_EMAIL='your-email@gmail.com'"
echo "  export FINCH_EMAIL_PASSWORD='your-app-password'"
echo "  export TELEGRAM_BOT_TOKEN='your-telegram-bot-token'"
echo ""
echo "— Finch"
