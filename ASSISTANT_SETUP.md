# PC Assistant & OpenRouter Setup Guide

This guide documents the VLM assistant loop setup for controlling the phone using OpenRouter.

## Overview
The backend includes an AI assistant that periodically captures the phone's screen, sends it to a VLM (Vision-Language Model) via OpenRouter, parses the next action (tap, swipe, write, sleep, home, back), and sends the action back to the phone.

## OpenRouter Configuration
- **API URL**: `https://openrouter.ai/api/v1/chat/completions`
- **API Key**: Configured via the `OPENROUTER_API_KEY` environment variable in the `.env` file on the remote server.
- **Model**: `google/gemini-2.5-flash`
  > [!IMPORTANT]
  > Do not use `google/gemini-2.5-flash:free` as it may result in a `404 Not Found` error.
- **Key Parameters**:
  - `max_tokens`: Set to `1000` (required to prevent `402 Payment Required` / token budget exhaustion errors on some tiers).
  - `response_format`: `{"type": "json_object"}` to enforce structured JSON output.

## How to Control the Assistant

### 1. Via the Web Dashboard
Open the Web Dashboard on your browser (served on port `10555`).
- Enter the goal in the "AI Assistant" section.
- Click **Start Assistant**.
- Monitor the live status and execution steps.
- Use **Stop Assistant** to cancel.

### 2. Via the Command Line
Run the standalone helper script:
```bash
# Activate virtual environment
source venv/bin/activate

# Start assistant with a single goal
python pc_assistant.py open telegram

# Or run interactive CLI mode
python pc_assistant.py
```
