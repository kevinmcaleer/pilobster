# 🦞 PiLobster

A lightweight, local AI assistant for Raspberry Pi — inspired by OpenClaw but built from scratch in Python.

PiLobster connects a local Ollama model to Telegram, letting you chat with your AI, schedule cron jobs, and generate code — all running on your own hardware with zero cloud dependencies.

Designed to run on a Raspberry Pi 5 with the Hailo AI HAT+ 2.

## Features

- **Telegram Chat** — Talk to your local LLM from anywhere via Telegram
- **Terminal UI (TUI)** — Claude-like chat interface directly in your terminal
- **Cron Scheduler** — Create recurring tasks via natural conversation ("remind me every morning at 8am to check the weather")
- **Code Workspace** — Ask it to generate code and it saves files to a local workspace folder
- **Persistent Memory** — Conversation history and task memory stored locally in SQLite
- **Keep-Alive** — Model stays loaded in memory (no cold-start delays)
- **Multi-Mode** — Run Telegram bot, TUI, or both simultaneously

## Requirements

- Python 3.11+
- Ollama installed and running
- A model pulled in Ollama (e.g. `ollama pull qwen2.5-instruct:1.5b`)
- A Telegram Bot Token (from [@BotFather](https://t.me/botfather)) — **Optional:** only needed for Telegram mode

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

# Run it (Both Telegram and TUI - default)
python -m pilobster

# Or run in Terminal UI mode only (no Telegram needed!)
python -m pilobster --mode tui

# Or run in Telegram mode only
python -m pilobster --mode telegram
```

## Using the Terminal UI

PiLobster includes a beautiful terminal UI powered by Textual, giving you a Claude-like chat experience directly in your terminal — no Telegram required!

### Running in TUI Mode

```bash
# Terminal UI only
python -m pilobster --mode tui

# Both modes (default) - conversations sync between Telegram and TUI
python -m pilobster --mode both

# Telegram only
python -m pilobster --mode telegram
```

**About "Both" Mode:**
When running both Telegram and TUI:
- Each interface has its own separate conversation history
- Telegram messages won't appear in TUI and vice versa
- Both share the same AI model, workspace, and scheduler
- Use `/quit` in TUI to exit cleanly

### TUI Commands & Shortcuts

**Commands:**
- `/quit` or `/exit` — Exit PiLobster
- `/clear` — Clear chat history
- `/status` — Show system status
- `/help` — Show help message

**Keyboard Shortcuts:**
- **Ctrl+C** — Quit
- **Ctrl+L** — Clear chat history
- **Ctrl+S** — Show system status
- **Enter** — Send message

### TUI Features

The Terminal UI supports all the same features as the Telegram bot:
- ✅ Natural conversation with markdown formatting
- ✅ Code generation with syntax highlighting
- ✅ File saving to workspace
- ✅ Cron job scheduling
- ✅ Persistent conversation history
- ✅ Real-time cron job notifications

### When to Use Which Mode

- **Both (`--mode both`)** *(default)*: Run both interfaces simultaneously with separate conversations
- **TUI only (`--mode tui`)**: Direct terminal access, no Telegram needed
- **Telegram only (`--mode telegram`)**: Access your AI only from your phone/Telegram

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

## Customizing Your Bot's Personality

PiLobster's personality and instructions are defined in `soul.md` — think of it as your bot's soul! 🦞

### Editing the Soul

```bash
nano soul.md
```

The `soul.md` file contains:
- Your bot's personality traits
- Instructions for special abilities (cron jobs, code generation)
- Examples that help the model understand what to do
- Behavioral guidelines

### What You Can Change

**Personality:**
```markdown
You are PiLobster, a helpful AI assistant running locally on a Raspberry Pi.
You are friendly, concise, and practical.
```
→ Make it formal, casual, pirate-themed, whatever you want!

**Special Instructions:**
- Modify how the bot responds to scheduling requests
- Change the format for code generation
- Add new capabilities or constraints
- Adjust verbosity and response style

### Tips for Small Models

If you're using a small model (like 1.5B-3B parameters):
- ✅ **Be very explicit** - Small models need clear, concrete examples
- ✅ **Show exact formats** - Include complete JSON/code examples
- ✅ **Keep it concise** - Long prompts can confuse small models
- ✅ **Test iteratively** - Try different phrasings if the model doesn't follow instructions

For larger models (7B+):
- More flexible with natural language instructions
- Can handle more complex personality traits
- Better at following nuanced guidelines

### Apply Changes

After editing `soul.md`:
```bash
# Restart PiLobster to load the new soul
python -m pilobster
```

The bot will reload `soul.md` on every restart, so you can experiment and iterate!

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

## Commands

In Telegram, you can use these commands:

- `/start` — Welcome message
- `/status` — Show system status (model, uptime, jobs)
- `/jobs` — List scheduled cron jobs
- `/schedule <cron> <message>` — Manually create a cron job
- `/cancel <id>` — Cancel a scheduled job
- `/workspace` — List files in the workspace
- `/save <filename>` — Manually save the last code block
- `/clear` — Clear chat history
- `/help` — Show available commands

### Creating Scheduled Jobs

You can ask the bot naturally ("Remind me every morning at 9am") or use the `/schedule` command for precise control:

```
/schedule */5 * * * * Check the temperature
/schedule 0 9 * * * Good morning!
/schedule 30 14 * * 1-5 Afternoon standup reminder
/schedule */3 * * * * Tell me a joke
```

**How it works:** The message you provide is sent to the AI as a prompt when the cron job triggers. So `/schedule */3 * * * * Tell me a joke` will ask the AI to tell you a joke every 3 minutes (and the AI will generate a different joke each time).

Cron format: `minute hour day month weekday`

Common patterns:
- `*/5 * * * *` — Every 5 minutes
- `0 * * * *` — Every hour
- `0 9 * * *` — Daily at 9am
- `0 9 * * 1` — Every Monday at 9am
- `0 9 * * 1-5` — Weekdays at 9am

Or just chat naturally:
- "Write a Python script that monitors CPU temperature"
- "Every day at 9am, tell me a fun fact"
- "What files are in my workspace?"

## License

MIT
