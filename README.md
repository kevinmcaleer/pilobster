# 🦞 PiLobster

A lightweight, local AI assistant for Raspberry Pi — inspired by OpenClaw but built from scratch in Python.

PiLobster connects a local Ollama model to Telegram, letting you chat with your AI, schedule cron jobs, and generate code — all running on your own hardware with zero cloud dependencies.

Designed to run on a Raspberry Pi 5 with the Hailo AI HAT+ 2.

## Features

- **Telegram Chat** — Talk to your local LLM from anywhere via Telegram
- **Cron Scheduler** — Create recurring tasks via natural conversation ("remind me every morning at 8am to check the weather")
- **Code Workspace** — Ask it to generate code and it saves files to a local workspace folder
- **Persistent Memory** — Conversation history and task memory stored locally in SQLite
- **Keep-Alive** — Model stays loaded in memory (no cold-start delays)

## Requirements

- Python 3.11+
- Ollama installed and running
- A Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- A model pulled in Ollama (e.g. `ollama pull qwen2.5-instruct:1.5b`)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/kevinmcaleer/pilobster.git
cd pilobster

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy the example config and edit it
cp config.example.yaml config.yaml
nano config.yaml  # Add your Telegram bot token and model name

# Run it
python -m pilobster
```

## Getting a Telegram Bot Token

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name and username for your bot
4. Copy the token BotFather gives you
5. Paste it into `config.yaml`

## Configuration

Edit `config.yaml`:

```yaml
telegram:
  token: "YOUR_BOT_TOKEN_HERE"

ollama:
  host: "http://localhost:11434" #or "http://localhost:8000" for Hailo AI HAT+2
  model: "tinyllama"
  keep_alive: -1        # Keep model loaded forever
  context_length: 4096

workspace:
  path: "./workspace"

scheduler:
  enabled: true

memory:
  database: "./pilobster.db"
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Telegram   │◄───►│  PiLobster   │◄───►│   Ollama    │
│  (mobile)   │     │   (Python)   │     │ (tinyllama) │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────┴───────┐
                    │   SQLite     │
                    │  (memory +   │
                    │   cron jobs) │
                    └──────────────┘
``` 

## Commands In Telegram, you can use these commands: - `/start` — Welcome message - `/status` — Show system status (model, uptime, jobs)
- `/jobs` — List scheduled cron jobs
- `/cancel <id>` — Cancel a scheduled job
- `/workspace` — List files in the workspace
- `/help` — Show available commands

Or just chat naturally:
- "Write a Python script that monitors CPU temperature"
- "Every day at 9am, tell me a fun fact"
- "What files are in my workspace?"

## License

MIT
