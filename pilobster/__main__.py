"""PiLobster — main entry point."""

import argparse
import asyncio
import logging
import sys

from .config import load_config
from .memory import Memory
from .agent import Agent
from .scheduler import Scheduler
from .workspace import Workspace

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


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="PiLobster — Local AI assistant for Raspberry Pi"
    )
    parser.add_argument(
        "--mode",
        choices=["telegram", "tui", "both"],
        default="telegram",
        help="Run mode: telegram bot, terminal UI, or both (default: telegram)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=0,
        help="User ID for TUI mode (default: 0)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    return parser.parse_args()


async def run_telegram_bot(config, agent, memory, scheduler, workspace):
    """Run the Telegram bot."""
    from .bot import TelegramBot

    bot = TelegramBot(config, agent, memory, scheduler, workspace)
    app = bot.build()

    logger.info("🦞 Telegram bot is ready! Waiting for messages...")

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
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


async def run_tui(config, agent, memory, scheduler, workspace, user_id):
    """Run the Terminal UI."""
    from .tui import PiLobsterTUI

    logger.info("🦞 Starting Terminal UI...")

    app = PiLobsterTUI(config, agent, memory, scheduler, workspace, user_id)

    # Set scheduler callback for TUI
    scheduler.set_send_callback(app.handle_scheduler_callback)

    await app.run_async()


async def main():
    """Initialise all components and start in selected mode."""
    args = parse_args()

    print(BANNER)
    print(f"Mode: {args.mode}")
    if args.mode in ["tui", "both"]:
        print(f"TUI User ID: {args.user_id}\n")

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error(e)
        sys.exit(1)

    # Validate config based on mode
    if args.mode in ["telegram", "both"]:
        if config.telegram.token == "YOUR_BOT_TOKEN_HERE" or not config.telegram.token:
            logger.error("Telegram mode requires a valid bot token in config.yaml")
            logger.error("Set your token or use --mode tui to skip Telegram")
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

    # Start selected mode(s)
    tasks = []

    if args.mode == "telegram":
        # Telegram only
        task = asyncio.create_task(
            run_telegram_bot(config, agent, memory, scheduler, workspace)
        )
        tasks.append(task)

    elif args.mode == "tui":
        # TUI only
        task = asyncio.create_task(
            run_tui(config, agent, memory, scheduler, workspace, args.user_id)
        )
        tasks.append(task)

    elif args.mode == "both":
        # Both modes concurrently
        telegram_task = asyncio.create_task(
            run_telegram_bot(config, agent, memory, scheduler, workspace)
        )
        tui_task = asyncio.create_task(
            run_tui(config, agent, memory, scheduler, workspace, args.user_id)
        )
        tasks.extend([telegram_task, tui_task])

    # Wait for all tasks
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # Cleanup
        logger.info("Shutting down...")
        scheduler.stop()
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
