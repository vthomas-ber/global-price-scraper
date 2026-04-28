#!/usr/bin/env bash
# Exit on error
set -o errexit

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing Playwright browsers..."
# Use python -m to ensure the command is found in the current environment
python -m playwright install chromium
python -m playwright install-deps chromium