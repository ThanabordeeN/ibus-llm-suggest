#!/usr/bin/env bash
# Wait for desktop session to be ready
sleep 5

# Start IBus daemon if not already running
if ! pgrep -x ibus-daemon > /dev/null; then
    ibus-daemon -dr --panel=disable
    sleep 3
fi

# Kill any old engine instance
pkill -f "LLM-auto-suggestion/engine/main.py" 2>/dev/null
sleep 1

# Start the LLM engine in background
python3 /home/cepheusn-22/Developments/LLM-auto-suggestion/engine/main.py &
sleep 2

# Activate the engine
ibus engine llm-suggest
