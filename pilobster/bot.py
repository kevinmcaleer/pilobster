"""Telegram bot — the user-facing interface for PiLobster."""

import logging
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
        self.app: Application | None = None

    def _is_allowed(self, user_id: int) -> bool:
        """Check if a user is allowed to use the bot."""
        allowed = self.config.telegram.allowed_users
        return not allowed or user_id in allowed

    async def _send_message(self, user_id: int, message: str):
        """Send a message to a user. Used as the scheduler callback."""
        if self.app:
            await self.app.bot.send_message(chat_id=user_id, text=message)

    # --- Command Handlers ---

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("Sorry, you're not authorised to use this bot.")
            return

        await update.message.reply_text(
            "🦞 *PiLobster is online!*\n\n"
            "I'm your local AI assistant running on a Raspberry Pi.\n\n"
            "Just send me a message to chat, or use:\n"
            "/status — System status\n"
            "/jobs — List scheduled tasks\n"
            "/workspace — List generated files\n"
            "/clear — Clear conversation history\n"
            "/help — Show all commands",
            parse_mode="Markdown",
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        if not self._is_allowed(update.effective_user.id):
            return

        jobs = await self.scheduler.list_jobs(update.effective_user.id)
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

    async def cmd_jobs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /jobs command — list scheduled cron jobs."""
        if not self._is_allowed(update.effective_user.id):
            return

        jobs = await self.scheduler.list_jobs(update.effective_user.id)
        if not jobs:
            await update.message.reply_text("No scheduled jobs. Ask me to schedule something!")
            return

        lines = ["🕐 *Scheduled Jobs*\n"]
        for job in jobs:
            lines.append(f"#{job['id']} — {job['task']}\n  Schedule: `{job['schedule']}`")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel <id> command — cancel a scheduled job."""
        if not self._is_allowed(update.effective_user.id):
            return

        if not context.args:
            await update.message.reply_text("Usage: /cancel <job_id>")
            return

        try:
            job_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Job ID must be a number.")
            return

        success = await self.scheduler.cancel_job(job_id)
        if success:
            await update.message.reply_text(f"✅ Cancelled job #{job_id}")
        else:
            await update.message.reply_text(f"Job #{job_id} not found.")

    async def cmd_workspace(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /workspace command — list files in workspace."""
        if not self._is_allowed(update.effective_user.id):
            return

        files = self.workspace.list_files()
        if not files:
            await update.message.reply_text("Workspace is empty. Ask me to write some code!")
            return

        lines = ["📁 *Workspace Files*\n"]
        for f in files:
            size_kb = f["size"] / 1024
            lines.append(f"`{f['name']}` ({size_kb:.1f} KB)")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /clear command — clear conversation history."""
        if not self._is_allowed(update.effective_user.id):
            return

        await self.memory.clear_history(update.effective_user.id)
        await update.message.reply_text("🧹 Conversation history cleared.")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        if not self._is_allowed(update.effective_user.id):
            return

        await update.message.reply_text(
            "🦞 *PiLobster Commands*\n\n"
            "/start — Welcome message\n"
            "/status — System status\n"
            "/jobs — List scheduled tasks\n"
            "/cancel <id> — Cancel a task\n"
            "/workspace — List generated files\n"
            "/clear — Clear chat history\n"
            "/help — This message\n\n"
            "*Natural Language:*\n"
            "• Ask me to write code — I'll save it to the workspace\n"
            "• Ask me to schedule something — I'll create a cron job\n"
            "• Or just chat!",
            parse_mode="Markdown",
        )

    # --- Message Handler ---

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages — the main chat loop."""
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            await update.message.reply_text("Sorry, you're not authorised.")
            return

        user_text = update.message.text
        logger.info(f"Message from {user_id}: {user_text[:80]}...")

        # Store user message
        await self.memory.add_message(user_id, "user", user_text)

        # Get conversation history
        history = await self.memory.get_history(
            user_id, self.config.memory.max_history
        )

        # Send typing indicator
        await update.message.chat.send_action("typing")

        # Get AI response
        response = await self.agent.chat(history)

        # Parse for cron jobs
        cron_jobs = self.agent.parse_cron_blocks(response)
        for job in cron_jobs:
            job_id = await self.scheduler.add_job(
                user_id, job["schedule"], job["task"], job["message"]
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

        # Send the cleaned response
        clean = self.agent.clean_response(response)
        if clean:
            # Telegram has a 4096 char limit — split if needed
            for i in range(0, len(clean), 4000):
                await update.message.reply_text(clean[i : i + 4000])

        # Store assistant response
        await self.memory.add_message(user_id, "assistant", response)

    # --- Bot Lifecycle ---

    async def post_init(self, app: Application):
        """Called after the bot is initialised — set up commands menu."""
        commands = [
            BotCommand("start", "Welcome message"),
            BotCommand("status", "System status"),
            BotCommand("jobs", "List scheduled tasks"),
            BotCommand("cancel", "Cancel a scheduled task"),
            BotCommand("workspace", "List generated files"),
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
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("workspace", self.cmd_workspace))
        self.app.add_handler(CommandHandler("clear", self.cmd_clear))
        self.app.add_handler(CommandHandler("help", self.cmd_help))

        # Register message handler (must be last)
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        # Set up scheduler callback
        self.scheduler.set_send_callback(self._send_message)

        return self.app
