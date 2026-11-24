#!/bin/bash

# Change to script directory
cd "$(dirname "$0")"

echo "🚀 Starting Data Bandar..."
echo "======================================"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "❌ Failed to create virtual environment"
        echo "Please ensure Python 3 is installed"
        read -p "Press any key to exit..."
        exit 1
    fi
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
source venv/bin/activate

# Check if requirements are installed
if [ ! -f "venv/.requirements_installed" ]; then
    echo "📦 Installing dependencies..."
    echo "   (This may take 2-3 minutes on first run)"
    pip install --upgrade pip
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        touch venv/.requirements_installed
        echo "✅ Dependencies installed"
    else
        echo "❌ Failed to install dependencies"
        read -p "Press any key to exit..."
        exit 1
    fi
fi

echo ""
echo "🎯 Launching Data Bandar..."
echo "======================================"
echo ""

# Run the control GUI
python control.py

echo ""
echo "👋 Data Bandar closed"
echo ""
read -p "Press any key to exit..."
