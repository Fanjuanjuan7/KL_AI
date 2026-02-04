#!/bin/bash
set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Go to project root
cd "$SCRIPT_DIR/.."

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting KL_AI...${NC}"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed.${NC}"
    exit 1
fi

# Ensure Python 3.9+
python3 -c "import sys; exit(0) if sys.version_info >= (3, 9) else exit(1)" || {
    echo -e "${RED}Error: Python 3.9+ is required.${NC}"
    exit 1
}

# Create required directories
mkdir -p logs config

# Virtual Environment Handling
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    echo -e "${GREEN}Installing dependencies...${NC}"
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# PM2 Check
if command -v pm2 &> /dev/null; then
    echo -e "${YELLOW}PM2 detected.${NC}"
    if [ -z "$PM2_SKIP" ]; then
        read -t 10 -p "Do you want to start with PM2? (y/N) " use_pm2 || use_pm2="n"
        echo ""
        if [[ "$use_pm2" =~ ^[Yy]$ ]]; then
            echo -e "${GREEN}Starting with PM2...${NC}"
            pm2 start src/gui_ctk.py --name "kl-ai" --interpreter python3
            pm2 save
            echo -e "${GREEN}Application started in background. Use 'pm2 log kl-ai' to view logs.${NC}"
            exit 0
        fi
    fi
fi

# Standard Start
echo -e "${GREEN}Launching application...${NC}"
export PYTHONPATH=$PYTHONPATH:$(pwd)
python3 -m src.gui_ctk
