"""Discord bot — an alternative user-facing interface for PiLobster."""

import logging
from typing import Optional

import discord
from discord.ext import commands

from .config import Config
from .agent import Agent
from .memory import Memory
from .scheduler import Scheduler
from .workspace import Workspace

logger = logging.getLogger("pilobster.discord")


class DiscordBot:
    """Discord bot that connects the user to the local AI agent."""

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
        self.channel_id: Optional[int] = None
        self.tui_callback = None

        # Set up discord intents
        intents = discord.Intents.default()
        intents.message_content = True

        self.bot = commands.Bot(
            command_prefix=self.config.discord.prefix,
            intents=intents,
            help_command=None,  # We provide our own
        )

        self._register_commands()
        self._register_events()

    def _is_allowed(self, user_id: int) -> bool:
        """Check if a user is allowed to use the bot."""
        allowed = self.config.discord.allowed_users
        return not allowed or user_id in allowed

    def _is_allowed_channel(self, channel_id: int) -> bool:
        """Check if a channel is allowed."""
        allowed = self.config.discord.allowed_channels
        return not allowed or channel_id in allowed

    def set_tui_callback(self, callback):
        """Set callback to send messages to TUI."""
        self.tui_callback = callback

    async def _send_to_tui(self, message: str, is_user: bool = False):
        """Send a message to TUI for display (in combined modes)."""
        if self.tui_callback:
            try:
                await self.tui_callback(message, is_user)
            except Exception as e:
                logger.error(f"Failed to send message to TUI: {e}")

    async def _send_to_channel(self, message: str):
        """Send a message to the Discord channel."""
        if self.channel_id is None:
            logger.debug("Cannot send to Discord: channel_id not set")
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.debug("Cannot send to Discord: channel not found")
            return

        try:
            for i in range(0, len(message), 2000):
                await channel.send(message[i : i + 2000])
        except Exception as e:
            logger.error(f"Failed to send message to Discord: {e}")

    async def _send_message(self, message: str):
        """Process a message through the agent. Used as the scheduler callback."""
        if self.channel_id is None:
            logger.debug("Cannot send cron message: channel_id not set")
            return

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return

        logger.info(f"Cron job triggered: {message}")

        await self.memory.add_message("user", message)
        history = await self.memory.get_history(self.config.memory.max_history)
        response = await self.agent.chat(history)

        # Parse for cron jobs
        cron_jobs, cron_errors = self.agent.parse_cron_blocks(response)
        if cron_errors:
            error_msg = "Cron job errors:\n" + "\n".join(f"- {e}" for e in cron_errors)
            await channel.send(error_msg)

        for job in cron_jobs:
            job_id = await self.scheduler.add_job(
                job["schedule"], job["task"], job["message"]
            )
            await channel.send(
                f"Scheduled job #{job_id}: {job['task']}\nSchedule: `{job['schedule']}`"
            )

        # Parse for file saves
        save_blocks = self.agent.parse_save_blocks(response)
        for block in save_blocks:
            filepath = await self.workspace.save_file(
                block["filename"], block["content"]
            )
            await channel.send(f"Saved `{filepath.name}` to workspace")

        # Parse for memory blocks
        memory_blocks = self.agent.parse_memory_blocks(response)
        for fact in memory_blocks:
            if await self.agent.save_to_memory(fact):
                await channel.send(f"Remembered: {fact}")

        clean = self.agent.clean_response(response)
        if clean:
            for i in range(0, len(clean), 2000):
                await channel.send(clean[i : i + 2000])

        await self.memory.add_message("assistant", response)

    def _register_events(self):
        """Register Discord event handlers."""

        @self.bot.event
        async def on_ready():
            logger.info(f"Discord bot logged in as {self.bot.user}")

        @self.bot.event
        async def on_message(message: discord.Message):
            # Ignore messages from the bot itself
            if message.author == self.bot.user:
                return

            # Check permissions
            if not self._is_allowed(message.author.id):
                return
            if not self._is_allowed_channel(message.channel.id):
                return

            # Process commands first
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                await self.bot.invoke(ctx)
                return

            # Skip messages that don't mention the bot and aren't in DMs
            if not isinstance(message.channel, discord.DMChannel):
                if self.bot.user not in message.mentions:
                    return

            # Store channel_id for scheduler callbacks
            if self.channel_id is None:
                self.channel_id = message.channel.id
                logger.info(f"Stored Discord channel_id: {self.channel_id}")

            # Strip the bot mention from the message text
            user_text = message.content
            if self.bot.user:
                user_text = user_text.replace(f"<@{self.bot.user.id}>", "").strip()

            if not user_text:
                return

            logger.info(f"Discord message received: {user_text[:80]}...")

            await self._send_to_tui(f"Discord: {user_text}", is_user=True)
            await self.memory.add_message("user", user_text)
            history = await self.memory.get_history(self.config.memory.max_history)

            # Show typing indicator
            async with message.channel.typing():
                response = await self.agent.chat(history)

            # Parse for cron jobs
            cron_jobs, cron_errors = self.agent.parse_cron_blocks(response)
            if cron_errors:
                error_msg = "Cron job errors:\n" + "\n".join(f"- {e}" for e in cron_errors)
                await message.channel.send(error_msg)

            for job in cron_jobs:
                job_id = await self.scheduler.add_job(
                    job["schedule"], job["task"], job["message"]
                )
                await message.channel.send(
                    f"Scheduled job #{job_id}: {job['task']}\nSchedule: `{job['schedule']}`"
                )

            # Parse for file saves
            save_blocks = self.agent.parse_save_blocks(response)
            for block in save_blocks:
                filepath = await self.workspace.save_file(
                    block["filename"], block["content"]
                )
                await message.channel.send(f"Saved `{filepath.name}` to workspace")

            # Parse for memory blocks
            memory_blocks = self.agent.parse_memory_blocks(response)
            for fact in memory_blocks:
                if await self.agent.save_to_memory(fact):
                    await message.channel.send(f"Remembered: {fact}")

            # Check memory size
            is_large, line_count = self.agent.check_memory_size()
            if is_large:
                await message.channel.send(
                    f"Your memory file is getting large ({line_count} lines).\n"
                    f"Consider using `{self.config.discord.prefix}forget` to clear old memories."
                )

            # Send cleaned response
            clean = self.agent.clean_response(response)
            if clean:
                await self._send_to_tui(clean, is_user=False)
                # Discord has a 2000 char limit
                for i in range(0, len(clean), 2000):
                    await message.channel.send(clean[i : i + 2000])

            await self.memory.add_message("assistant", response)

    def _register_commands(self):
        """Register Discord slash/prefix commands."""

        @self.bot.command(name="start")
        async def cmd_start(ctx: commands.Context):
            """Welcome message."""
            if not self._is_allowed(ctx.author.id):
                return
            message = (
                "**PiLobster is online!**\n\n"
                "I'm your local AI assistant running on a Raspberry Pi.\n\n"
                "Just mention me or DM me to chat, or use:\n"
                f"`{self.config.discord.prefix}status` — System status\n"
                f"`{self.config.discord.prefix}jobs` — List scheduled tasks\n"
                f"`{self.config.discord.prefix}schedule` — Create a cron job\n"
                f"`{self.config.discord.prefix}workspace` — List generated files\n"
                f"`{self.config.discord.prefix}clear` — Clear conversation history\n"
                f"`{self.config.discord.prefix}help` — Show all commands"
            )
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="status")
        async def cmd_status(ctx: commands.Context):
            """System status."""
            if not self._is_allowed(ctx.author.id):
                return
            jobs = await self.scheduler.list_jobs()
            files = self.workspace.list_files()
            message = (
                f"**PiLobster Status**\n\n"
                f"Model: `{self.config.ollama.model}`\n"
                f"Host: `{self.config.ollama.host}`\n"
                f"Context: `{self.config.ollama.context_length}` tokens\n"
                f"Scheduled jobs: `{len(jobs)}`\n"
                f"Workspace files: `{len(files)}`"
            )
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="jobs")
        async def cmd_jobs(ctx: commands.Context):
            """List scheduled cron jobs."""
            if not self._is_allowed(ctx.author.id):
                return
            jobs = await self.scheduler.list_jobs()
            if not jobs:
                message = "No scheduled jobs. Ask me to schedule something!"
                await ctx.send(message)
                await self._send_to_tui(message, is_user=False)
                return

            lines = ["**Scheduled Jobs**\n"]
            for job in jobs:
                lines.append(f"#{job['id']} — {job['task']}\n  Schedule: `{job['schedule']}`")
            message = "\n".join(lines)
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="cancel")
        async def cmd_cancel(ctx: commands.Context, job_id: int = None):
            """Cancel a scheduled job."""
            if not self._is_allowed(ctx.author.id):
                return
            if job_id is None:
                await ctx.send(f"Usage: `{self.config.discord.prefix}cancel <job_id>`")
                return

            success = await self.scheduler.cancel_job(job_id)
            message = f"Cancelled job #{job_id}" if success else f"Job #{job_id} not found."
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="workspace")
        async def cmd_workspace(ctx: commands.Context):
            """List files in workspace."""
            if not self._is_allowed(ctx.author.id):
                return
            files = self.workspace.list_files()
            if not files:
                message = "Workspace is empty. Ask me to write some code!"
                await ctx.send(message)
                await self._send_to_tui(message, is_user=False)
                return

            lines = ["**Workspace Files**\n"]
            for f in files:
                size_kb = f["size"] / 1024
                lines.append(f"`{f['name']}` ({size_kb:.1f} KB)")
            message = "\n".join(lines)
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="clear")
        async def cmd_clear(ctx: commands.Context):
            """Clear conversation history."""
            if not self._is_allowed(ctx.author.id):
                return
            await self.memory.clear_history()
            message = "Conversation history cleared."
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="save")
        async def cmd_save(ctx: commands.Context, filename: str = None):
            """Save last code block to workspace."""
            if not self._is_allowed(ctx.author.id):
                return
            if not filename:
                await ctx.send(
                    f"Usage: `{self.config.discord.prefix}save filename.py`\n"
                    "This will save the last code block from my response."
                )
                return

            history = await self.memory.get_history(limit=10)
            last_code = None
            for msg in reversed(history):
                if msg["role"] == "assistant":
                    code_blocks = self.agent.extract_code_blocks(msg["content"])
                    if code_blocks:
                        last_code = code_blocks[0]["content"]
                        break

            if not last_code:
                await ctx.send("No code blocks found in recent conversation. Ask me to write some code first!")
                return

            try:
                filepath = await self.workspace.save_file(filename, last_code)
                await ctx.send(f"Saved `{filepath.name}` to workspace")
            except Exception as e:
                await ctx.send(f"Error saving file: {e}")

        @self.bot.command(name="schedule")
        async def cmd_schedule(ctx: commands.Context, *args):
            """Manually create a cron job."""
            if not self._is_allowed(ctx.author.id):
                return
            prefix = self.config.discord.prefix
            if len(args) < 6:
                message = (
                    f"Usage: `{prefix}schedule <cron> <prompt>`\n\n"
                    "The prompt will be sent to me when the job triggers.\n\n"
                    "Cron format: `minute hour day month weekday`\n\n"
                    "Examples:\n"
                    f"`{prefix}schedule */3 * * * * Tell me a joke`\n"
                    f"`{prefix}schedule 0 9 * * * Give me a motivational quote`\n"
                    f"`{prefix}schedule 30 14 * * 1-5 Remind me to stand up`"
                )
                await ctx.send(message)
                return

            schedule = " ".join(args[:5])
            prompt = " ".join(args[5:])

            if not prompt:
                await ctx.send(f"Prompt cannot be empty.\nUsage: `{prefix}schedule <cron> <prompt>`")
                return

            task = prompt[:50] + "..." if len(prompt) > 50 else prompt

            try:
                job_id = await self.scheduler.add_job(schedule, task, prompt)
                await ctx.send(
                    f"Scheduled job #{job_id}: {task}\n"
                    f"Schedule: `{schedule}`\n"
                    f"Message: {prompt}"
                )
            except ValueError as e:
                await ctx.send(
                    f"Invalid cron expression: {e}\n\n"
                    "Cron format: `minute hour day month weekday`\n"
                    "Example: `*/3 * * * *` (every 3 minutes)"
                )
            except Exception as e:
                await ctx.send(f"Error creating job: {e}")

        @self.bot.command(name="help")
        async def cmd_help(ctx: commands.Context):
            """Show all commands."""
            if not self._is_allowed(ctx.author.id):
                return
            prefix = self.config.discord.prefix
            message = (
                "**PiLobster Commands**\n\n"
                f"`{prefix}start` — Welcome message\n"
                f"`{prefix}status` — System status\n"
                f"`{prefix}jobs` — List scheduled tasks\n"
                f"`{prefix}schedule <cron> <msg>` — Create a cron job\n"
                f"`{prefix}cancel <id>` — Cancel a task\n"
                f"`{prefix}workspace` — List generated files\n"
                f"`{prefix}save <filename>` — Save last code block\n"
                f"`{prefix}memory` — View saved memories\n"
                f"`{prefix}forget` — Clear all memories\n"
                f"`{prefix}clear` — Clear chat history\n"
                f"`{prefix}help` — This message\n\n"
                "**Natural Language:**\n"
                "- Mention me or DM me to chat\n"
                "- Ask me to write code — I'll save it to the workspace\n"
                "- Ask me to schedule something — I'll create a cron job"
            )
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="memory")
        async def cmd_memory(ctx: commands.Context):
            """Show saved memories."""
            if not self._is_allowed(ctx.author.id):
                return
            if self.agent.memory_content:
                is_large, line_count = self.agent.check_memory_size()
                header = f"**My Memory ({line_count} lines)**\n\n"
                if is_large:
                    header += "_Memory is getting large!_\n\n"
                message = header + self.agent.memory_content
            else:
                message = "**My Memory**\n\nNo memories saved yet. Tell me something about yourself!"

            # Discord 2000 char limit
            for i in range(0, len(message), 2000):
                await ctx.send(message[i : i + 2000])
            await self._send_to_tui(message, is_user=False)

        @self.bot.command(name="forget")
        async def cmd_forget(ctx: commands.Context):
            """Clear all memories."""
            if not self._is_allowed(ctx.author.id):
                return
            if self.agent.clear_memory():
                message = "All memories have been forgotten."
            else:
                message = "Failed to clear memory."
            await ctx.send(message)
            await self._send_to_tui(message, is_user=False)

    async def start(self):
        """Start the Discord bot."""
        self.scheduler.set_send_callback(self._send_message)
        logger.info("Starting Discord bot...")
        await self.bot.start(self.config.discord.token)

    async def close(self):
        """Stop the Discord bot."""
        await self.bot.close()
