"""PiLobster — main entry point."""

import asyncio
import logging
import sys

from .config import load_config
from .memory import Memory
from .agent import Agent
from .scheduler import Scheduler
from .workspace import Workspace
from .bot import TelegramBot

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pilobster")

BANNER = r"""
  ____  _ _          _         _
 |  _ \(_) |    ___ | |__  ___| |_ ___ _ __
 | |_) | | |   / _ \| '_ \/ __| __/ _ \ '__|
 |  __/| | |__| (_) | |_) \__ \ ||  __/ |
 |_|   |_|_____\___/|_.__/|___/\__\___|_|

 🦞 Your local AI assistant — v0.1.0
"""


async def main():
    """Initialise all components and start the bot."""
    print(BANNER)

    # Load config
    try:
        config = load_config()
    except FileNotFoundError as e:
        logger.error(e)
        sys.exit(1)

    if config.telegram.token == "YOUR_BOT_TOKEN_HERE" or not config.telegram.token:
        logger.error("Please set your Telegram bot token in config.yaml")
        sys.exit(1)

    # Initialise memory
    memory = Memory(config.memory.database)
    await memory.connect()
    logger.info(f"Database connected: {config.memory.database}")

    # Initialise agent
    agent = Agent(config.ollama, config.system_prompt)
    await agent.warm_up()

    # Initialise workspace
    workspace = Workspace(config.workspace.path, memory)
    logger.info(f"Workspace: {workspace.path.resolve()}")

    # Initialise scheduler
    scheduler = Scheduler(memory)
    if config.scheduler.enabled:
        await scheduler.load_jobs()
        scheduler.start()

    # Build and run the Telegram bot
    bot = TelegramBot(config, agent, memory, scheduler, workspace)
    app = bot.build()

    logger.info("🦞 PiLobster is ready! Waiting for messages...")

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Keep running until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
    finally:
        logger.info("Shutting down...")
        scheduler.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await memory.close()
        logger.info("Goodbye! 🦞")


def run():
    """Entry point for running PiLobster."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
