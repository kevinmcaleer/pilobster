"""Telegram bot — the user-facing interface for PiLobster."""

import logging
from typing import Optional
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import Config
from .agent import Agent
from .memory import Memory
from .scheduler import Scheduler
from .workspace import Workspace

logger = logging.getLogger("pilobster.telegram")


class TelegramBot:
    """Telegram bot that connects the user to the local AI agent."""

    def __init__(
        self,
        config: Config,
        agent: Agent,
        memory: Memory,
        scheduler: Scheduler,
        workspace: Workspace,
    ):
        self.config = config
        self.agent = agent
        self.memory = memory
        self.scheduler = scheduler
        self.workspace = workspace
        self.app: Optional[Application] = None
        self.chat_id: Optional[int] = None  # Telegram chat ID for sending messages
        self.tui_callback = None  # Callback to send messages to TUI

    def _is_allowed(self, user_id: int) -> bool:
        """Check if a user is allowed to use the bot.

        In single-user mode, everyone shares the same conversation.
        You can still restrict access using allowed_users in config.
        """
        allowed = self.config.telegram.allowed_users
        return not allowed or user_id in allowed

    def set_tui_callback(self, callback):
        """Set callback to send messages to TUI.

        Callback should accept (message: str, is_user: bool) where is_user
        indicates if this is a user message (True) or assistant/system message (False).
        """
        self.tui_callback = callback

    async def _send_to_tui(self, message: str, is_user: bool = False):
        """Send a message to TUI for display (in "both" mode)."""
        if self.tui_callback:
            try:
                await self.tui_callback(message, is_user)
            except Exception as e:
                logger.error(f"Failed to send message to TUI: {e}")

    async def _send_to_telegram(self, message: str):
        """Send a message to Telegram without processing it through the agent.

        This is used by TUI to display messages in Telegram (in "both" mode).
        """
        if not self.app or self.chat_id is None:
            logger.debug("Cannot send to Telegram: app not initialized or chat_id not set")
            return

        try:
            # Send message to Telegram, splitting if too long
            for i in range(0, len(message), 4000):
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=message[i : i + 4000]
                )
        except Exception as e:
            logger.error(f"Failed to send message to Telegram: {e}")

    async def _send_message(self, message: str):
        """Send a message to the user. Used as the scheduler callback.

        This processes the message as if the user sent it, so the AI
        generates a response instead of just echoing the message.
        """
        if not self.app or self.chat_id is None:
            logger.debug("Cannot send cron message: app not initialized or chat_id not set")
            return

        logger.info(f"Cron job triggered: {message}")

        # Store the prompt in history as a user message
        await self.memory.add_message("user", message)

        # Get conversation history
        history = await self.memory.get_history(self.config.memory.max_history)

        # Get AI response
        response = await self.agent.chat(history)

        # Parse for cron jobs (in case the AI creates new jobs)
        cron_jobs, cron_errors = self.agent.parse_cron_blocks(response)

        # Show validation errors if any
        if cron_errors:
            error_msg = "⚠️ Cron job errors:\n" + "\n".join(f"• {e}" for e in cron_errors)
            await self.app.bot.send_message(chat_id=self.chat_id, text=error_msg)

        # Create valid jobs
        for job in cron_jobs:
            job_id = await self.scheduler.add_job(
                job["schedule"], job["task"], job["message"]
            )
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=f"✅ Scheduled job #{job_id}: {job['task']}\n"
                     f"Schedule: `{job['schedule']}`",
                parse_mode="Markdown",
            )

        # Parse for file saves
        save_blocks = self.agent.parse_save_blocks(response)
        for block in save_blocks:
            filepath = await self.workspace.save_file(
                block["filename"], block["content"]
            )
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=f"💾 Saved `{filepath.name}` to workspace",
                parse_mode="Markdown",
            )

        # Parse for memory blocks
        memory_blocks = self.agent.parse_memory_blocks(response)
        for fact in memory_blocks:
            if await self.agent.save_to_memory(fact):
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"🧠 Remembered: {fact}"
                )

        # Send the cleaned response
        clean = self.agent.clean_response(response)
        if clean:
            # Telegram has a 4096 char limit — split if needed
            for i in range(0, len(clean), 4000):
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=clean[i : i + 4000]
                )

        # Store assistant response
        await self.memory.add_message("assistant", response)

    # --- Command Handlers ---

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        message = (
            "🦞 *PiLobster is online!*\n\n"
            "I'm your local AI assistant running on a Raspberry Pi.\n\n"
            "Just send me a message to chat, or use:\n"
            "/status — System status\n"
            "/jobs — List scheduled tasks\n"
            "/schedule — Create a cron job\n"
            "/workspace — List generated files\n"
            "/clear — Clear conversation history\n"
            "/help — Show all commands"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
        await self._send_to_tui(message, is_user=False)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        jobs = await self.scheduler.list_jobs()
        files = self.workspace.list_files()

        status = (
            f"🦞 *PiLobster Status*\n\n"
            f"Model: `{self.config.ollama.model}`\n"
            f"Host: `{self.config.ollama.host}`\n"
            f"Context: `{self.config.ollama.context_length}` tokens\n"
            f"Scheduled jobs: `{len(jobs)}`\n"
            f"Workspace files: `{len(files)}`"
        )
        await update.message.reply_text(status, parse_mode="Markdown")
        await self._send_to_tui(status, is_user=False)

    async def cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /jobs command — list scheduled cron jobs."""
        jobs = await self.scheduler.list_jobs()
        if not jobs:
            message = "No scheduled jobs. Ask me to schedule something!"
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)
            return

        lines = ["🕐 *Scheduled Jobs*\n"]
        for job in jobs:
            lines.append(f"#{job['id']} — {job['task']}\n  Schedule: `{job['schedule']}`")

        message = "\n".join(lines)
        await update.message.reply_text(message, parse_mode="Markdown")
        await self._send_to_tui(message, is_user=False)

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel <id> command — cancel a scheduled job."""
        if not context.args:
            message = "Usage: /cancel <job_id>"
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)
            return

        try:
            job_id = int(context.args[0])
        except ValueError:
            message = "Job ID must be a number."
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)
            return

        success = await self.scheduler.cancel_job(job_id)
        if success:
            message = f"✅ Cancelled job #{job_id}"
        else:
            message = f"Job #{job_id} not found."
        await update.message.reply_text(message)
        await self._send_to_tui(message, is_user=False)

    async def cmd_workspace(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /workspace command — list files in workspace."""
        files = self.workspace.list_files()
        if not files:
            message = "Workspace is empty. Ask me to write some code!"
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)
            return

        lines = ["📁 *Workspace Files*\n"]
        for f in files:
            size_kb = f["size"] / 1024
            lines.append(f"`{f['name']}` ({size_kb:.1f} KB)")

        message = "\n".join(lines)
        await update.message.reply_text(message, parse_mode="Markdown")
        await self._send_to_tui(message, is_user=False)

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /clear command — clear conversation history."""
        await self.memory.clear_history()
        message = "🧹 Conversation history cleared."
        await update.message.reply_text(message)
        await self._send_to_tui(message, is_user=False)

    async def cmd_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /save command — manually save code from last response."""
        # Check if filename was provided
        if not context.args:
            message = (
                "Usage: `/save filename.py`\n"
                "This will save the last code block from my response."
            )
            await update.message.reply_text(message, parse_mode="Markdown")
            await self._send_to_tui(message, is_user=False)
            return

        filename = context.args[0]

        # Get recent history to find the last assistant message
        history = await self.memory.get_history(limit=10)

        # Find the most recent assistant message with code
        last_code = None
        for msg in reversed(history):  # Start from most recent
            if msg["role"] == "assistant":
                code_blocks = self.agent.extract_code_blocks(msg["content"])
                if code_blocks:
                    last_code = code_blocks[0]["content"]
                    break

        if not last_code:
            message = (
                "❌ No code blocks found in recent conversation. "
                "Ask me to write some code first!"
            )
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)
            return

        # Save the code
        try:
            filepath = await self.workspace.save_file(filename, last_code)
            message = f"💾 Saved `{filepath.name}` to workspace"
            await update.message.reply_text(message, parse_mode="Markdown")
            await self._send_to_tui(message, is_user=False)
        except Exception as e:
            message = f"❌ Error saving file: {e}"
            await update.message.reply_text(message)
            await self._send_to_tui(message, is_user=False)

    async def cmd_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /schedule command — manually create a cron job."""
        # Check if arguments were provided
        if not context.args or len(context.args) < 6:
            message = (
                "Usage: `/schedule <cron> <prompt>`\n\n"
                "The prompt will be sent to me when the job triggers.\n\n"
                "Cron format: `minute hour day month weekday`\n\n"
                "Examples:\n"
                "`/schedule */3 * * * * Tell me a joke`\n"
                "`/schedule 0 9 * * * Give me a motivational quote`\n"
                "`/schedule 30 14 * * 1-5 Remind me to stand up`\n\n"
                "Common patterns:\n"
                "• `*/5 * * * *` — Every 5 minutes\n"
                "• `0 * * * *` — Every hour\n"
                "• `0 9 * * *` — Daily at 9am\n"
                "• `0 9 * * 1` — Every Monday at 9am"
            )
            await update.message.reply_text(message, parse_mode="Markdown")
            await self._send_to_tui(message, is_user=False)
            return

        # Parse cron expression (first 5 args) and message (remaining args)
        cron_parts = context.args[:5]
        message_parts = context.args[5:]

        schedule = " ".join(cron_parts)
        message = " ".join(message_parts)

        if not message:
            error_message = (
                "❌ Prompt cannot be empty.\n"
                "Usage: `/schedule <cron> <prompt>`"
            )
            await update.message.reply_text(error_message, parse_mode="Markdown")
            await self._send_to_tui(error_message, is_user=False)
            return

        # Create a task description from the message (truncate if needed)
        task = message[:50] + "..." if len(message) > 50 else message

        # Validate and create the job
        try:
            job_id = await self.scheduler.add_job(schedule, task, message)
            success_message = (
                f"✅ Scheduled job #{job_id}: {task}\n"
                f"Schedule: `{schedule}`\n"
                f"Message: {message}"
            )
            await update.message.reply_text(success_message, parse_mode="Markdown")
            await self._send_to_tui(success_message, is_user=False)
        except ValueError as e:
            error_message = (
                f"❌ Invalid cron expression: {e}\n\n"
                f"Cron format: `minute hour day month weekday`\n"
                f"Example: `*/3 * * * *` (every 3 minutes)"
            )
            await update.message.reply_text(error_message, parse_mode="Markdown")
            await self._send_to_tui(error_message, is_user=False)
        except Exception as e:
            error_message = f"❌ Error creating job: {e}"
            await update.message.reply_text(error_message)
            await self._send_to_tui(error_message, is_user=False)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        message = (
            "🦞 *PiLobster Commands*\n\n"
            "/start — Welcome message\n"
            "/status — System status\n"
            "/jobs — List scheduled tasks\n"
            "/schedule <cron> <msg> — Create a cron job\n"
            "/cancel <id> — Cancel a task\n"
            "/workspace — List generated files\n"
            "/save <filename> — Save last code block\n"
            "/memory — View saved memories\n"
            "/forget — Clear all memories\n"
            "/clear — Clear chat history\n"
            "/help — This message\n\n"
            "*Natural Language:*\n"
            "• Ask me to write code — I'll save it to the workspace\n"
            "• Ask me to schedule something — I'll create a cron job\n"
            "• Or just chat!"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
        await self._send_to_tui(message, is_user=False)

    async def cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /memory command — show saved memories."""
        if self.agent.memory_content:
            is_large, line_count = self.agent.check_memory_size()
            header = f"🧠 *My Memory ({line_count} lines)*\n\n"
            if is_large:
                header += "⚠️ _Memory is getting large!_\n\n"
            message = header + self.agent.memory_content
        else:
            message = "🧠 *My Memory*\n\nNo memories saved yet. Tell me something about yourself!"

        await update.message.reply_text(message, parse_mode="Markdown")
        await self._send_to_tui(message, is_user=False)

    async def cmd_forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /forget command — clear all memories."""
        if self.agent.clear_memory():
            message = "🧹 All memories have been forgotten."
        else:
            message = "❌ Failed to clear memory."

        await update.message.reply_text(message)
        await self._send_to_tui(message, is_user=False)

    # --- Message Handler ---

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages — the main chat loop."""
        # Store chat_id for later use (for sending messages from TUI)
        if self.chat_id is None:
            self.chat_id = update.effective_chat.id
            logger.info(f"Stored Telegram chat_id: {self.chat_id}")

        user_text = update.message.text
        logger.info(f"Message received: {user_text[:80]}...")

        # Send user message to TUI
        await self._send_to_tui(f"📱 Telegram: {user_text}", is_user=True)

        # Store user message
        await self.memory.add_message("user", user_text)

        # Get conversation history
        history = await self.memory.get_history(self.config.memory.max_history)

        # Send typing indicator
        await update.message.chat.send_action("typing")

        # Get AI response
        response = await self.agent.chat(history)

        # Parse for cron jobs
        cron_jobs, cron_errors = self.agent.parse_cron_blocks(response)

        # Show validation errors if any
        if cron_errors:
            error_msg = "⚠️ Cron job errors:\n" + "\n".join(f"• {e}" for e in cron_errors)
            await update.message.reply_text(error_msg)

        # Create valid jobs
        for job in cron_jobs:
            job_id = await self.scheduler.add_job(
                job["schedule"], job["task"], job["message"]
            )
            await update.message.reply_text(
                f"✅ Scheduled job #{job_id}: {job['task']}\n"
                f"Schedule: `{job['schedule']}`",
                parse_mode="Markdown",
            )

        # Parse for file saves
        save_blocks = self.agent.parse_save_blocks(response)
        for block in save_blocks:
            filepath = await self.workspace.save_file(
                block["filename"], block["content"]
            )
            await update.message.reply_text(
                f"💾 Saved `{filepath.name}` to workspace",
                parse_mode="Markdown",
            )

        # Parse for memory blocks
        memory_blocks = self.agent.parse_memory_blocks(response)
        for fact in memory_blocks:
            if await self.agent.save_to_memory(fact):
                await update.message.reply_text(f"🧠 Remembered: {fact}")

        # Check if memory is getting too large
        is_large, line_count = self.agent.check_memory_size()
        if is_large:
            await update.message.reply_text(
                f"⚠️ Your memory file is getting large ({line_count} lines).\n"
                f"Consider using `/forget` to clear old memories.",
                parse_mode="Markdown",
            )

        # Send the cleaned response
        clean = self.agent.clean_response(response)
        if clean:
            # Send to TUI
            await self._send_to_tui(clean, is_user=False)
            # Telegram has a 4096 char limit — split if needed
            for i in range(0, len(clean), 4000):
                await update.message.reply_text(clean[i : i + 4000])

        # Store assistant response
        await self.memory.add_message("assistant", response)

    # --- Bot Lifecycle ---

    async def post_init(self, app: Application):
        """Called after the bot is initialised — set up commands menu."""
        commands = [
            BotCommand("start", "Welcome message"),
            BotCommand("status", "System status"),
            BotCommand("jobs", "List scheduled tasks"),
            BotCommand("schedule", "Create a cron job"),
            BotCommand("cancel", "Cancel a scheduled task"),
            BotCommand("workspace", "List generated files"),
            BotCommand("save", "Save last code block"),
            BotCommand("memory", "View saved memories"),
            BotCommand("forget", "Clear all memories"),
            BotCommand("clear", "Clear chat history"),
            BotCommand("help", "Show commands"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("Bot commands menu registered")

    def build(self) -> Application:
        """Build the Telegram application with all handlers."""
        self.app = (
            Application.builder()
            .token(self.config.telegram.token)
            .post_init(self.post_init)
            .build()
        )

        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("jobs", self.cmd_jobs))
        self.app.add_handler(CommandHandler("schedule", self.cmd_schedule))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("workspace", self.cmd_workspace))
        self.app.add_handler(CommandHandler("save", self.cmd_save))
        self.app.add_handler(CommandHandler("memory", self.cmd_memory))
        self.app.add_handler(CommandHandler("forget", self.cmd_forget))
        self.app.add_handler(CommandHandler("clear", self.cmd_clear))
        self.app.add_handler(CommandHandler("help", self.cmd_help))

        # Register message handler (must be last)
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        # Set up scheduler callback
        self.scheduler.set_send_callback(self._send_message)

        return self.app
