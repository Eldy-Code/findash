#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it to add your Reddit API credentials."
fi

# Create virtualenv if it doesn't exist
if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install/upgrade dependencies
pip install -q -r requirements.txt

echo ""
echo "========================================="
echo "  FinDash — Financial Dashboard"
echo "========================================="
echo "  Dashboard: http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs"
echo "========================================="
echo ""

uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
