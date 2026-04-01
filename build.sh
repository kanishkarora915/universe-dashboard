#!/bin/bash
# Build script for Render deployment
# 1. Install Node deps + build React frontend
# 2. Install Python deps

echo "=== Building React frontend ==="
npm install
npm run build

echo "=== Installing Python dependencies ==="
pip install -r backend/requirements.txt

echo "=== Build complete ==="
