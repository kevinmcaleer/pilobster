"""Terminal UI for PiLobster using Textual."""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Header, Footer, Static, Input, RichLog
from textual.binding import Binding
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text

from .config import Config
from .agent import Agent
from .memory import Memory
from .scheduler import Scheduler
from .workspace import Workspace

logger = logging.getLogger("pilobster.tui")


class PiLobsterTUI(App):
    """A Textual TUI for PiLobster AI assistant."""

    CSS = """
    Screen {
        background: $background;
        overflow-x: hidden;
    }

    #chat_container {
        height: 1fr;
        border: solid $primary;
        background: $surface;
        width: 100%;
        overflow-x: hidden;
    }

    #chat_log {
        height: 1fr;
        background: $surface;
        border: none;
        padding: 0 1;
        width: 100%;
        overflow-x: auto;
        overflow-y: auto;
    }

    #user_input {
        dock: bottom;
        border: solid $primary;
        height: 3;
        margin: 0 0;
        width: 100%;
    }

    .user-message {
        background: $boost;
        border: solid $primary;
        margin: 1 0;
    }

    .assistant-message {
        background: $panel;
        border: solid $accent;
        margin: 1 0;
    }

    .status-message {
        color: $text-muted;
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_history", "Clear", show=True),
        Binding("ctrl+s", "show_status", "Status", show=True),
        Binding("ctrl+q", "quit", "Quit", show=False),  # Alternative quit
    ]

    def __init__(
        self,
        config: Config,
        agent: Agent,
        memory: Memory,
        scheduler: Scheduler,
        workspace: Workspace,
        user_id: int = 0,
    ):
        super().__init__()
        self.config = config
        self.agent = agent
        self.memory = memory
        self.scheduler = scheduler
        self.workspace = workspace
        self.user_id = user_id
        self.processing = False
        self.title = "🦞 PiLobster"
        self.sub_title = f"Local AI Assistant (user_id={user_id})"
        self.last_message_count = 0  # Track messages for sync

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)
        with ScrollableContainer(id="chat_container"):
            yield RichLog(id="chat_log", wrap=True, markup=True, highlight=True)
        yield Input(placeholder="Type your message...", id="user_input")
        yield Footer()

    async def on_mount(self) -> None:
        """Called when app starts."""
        chat_log = self.query_one("#chat_log", RichLog)

        # Welcome message
        welcome = Panel(
            Markdown(
                f"""# Welcome to PiLobster! 🦞\n\nI'm your local AI assistant running on **{self.config.ollama.model}**.\n\n**Commands:**\n- `/quit` - Exit PiLobster\n- `/clear` - Clear chat history\n- `/status` - Show system status\n- `/help` - Show help\n\n**Keyboard Shortcuts:**\n- `Ctrl+L` - Clear history\n- `Ctrl+S` - Status\n- `Ctrl+C` - Quit\n\nJust start typing to chat with me!"""
            ),
            title="🦞 PiLobster",
            border_style="green",
            width=76,
            expand=False,
        )
        chat_log.write(welcome)

        # Load conversation history
        history = await self.memory.get_history(self.user_id, limit=20)
        self.last_message_count = len(history)
        if history:
            chat_log.write("\n")
            chat_log.write(
                Text("─── Previous Conversation ───", style="dim italic"),
                shrink=True,
            )
            for msg in history:
                role = "You" if msg["role"] == "user" else "PiLobster 🦞"
                border_style = "blue" if msg["role"] == "user" else "magenta"
                content = msg["content"]

                # Clean response for display
                if msg["role"] == "assistant":
                    content = self.agent.clean_response(content)

                panel = Panel(
                    Markdown(content) if content.strip() else Text("(empty)", style="dim"),
                    title=f"[bold]{role}[/bold]",
                    border_style=border_style,
                    width=76,
                    expand=False,
                )
                chat_log.write(panel)

        # Focus input
        self.query_one("#user_input", Input).focus()

        # Start background sync check (every 2 seconds)
        self.set_interval(2.0, self.check_for_new_messages)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user message submission."""
        if self.processing:
            return

        user_text = event.value.strip()
        if not user_text:
            return

        # Clear input
        event.input.value = ""

        # Handle slash commands
        if user_text.startswith("/"):
            await self.handle_command(user_text)
        else:
            # Process message
            await self.process_message(user_text)

    async def handle_command(self, command: str):
        """Handle slash commands like /quit, /status, etc."""
        parts = command.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if cmd == "/quit" or cmd == "/exit":
            self.exit()

        elif cmd == "/clear":
            self.action_clear_history()

        elif cmd == "/status":
            self.action_show_status()

        elif cmd == "/jobs":
            await self.cmd_jobs()

        elif cmd == "/schedule":
            await self.cmd_schedule(args)

        elif cmd == "/cancel":
            await self.cmd_cancel(args)

        elif cmd == "/workspace":
            await self.cmd_workspace()

        elif cmd == "/save":
            await self.cmd_save(args)

        elif cmd == "/help":
            help_text = """**Available Commands:**

- `/quit` or `/exit` — Exit PiLobster
- `/clear` — Clear chat history
- `/status` — Show system status
- `/jobs` — List scheduled cron jobs
- `/schedule <cron> <message>` — Create a cron job
- `/cancel <id>` — Cancel a cron job
- `/workspace` — List workspace files
- `/save <filename>` — Save last code block
- `/help` — Show this help message

**Keyboard Shortcuts:**
- `Ctrl+C` — Quit
- `Ctrl+L` — Clear history
- `Ctrl+S` — Show status"""
            await self.display_message("assistant", help_text)

        else:
            # Unknown command, process as normal message
            await self.process_message(command)

    async def process_message(self, user_text: str):
        """Process user message through agent and display response."""
        self.processing = True

        try:
            # Display user message
            await self.display_message("user", user_text)

            # Store user message
            await self.memory.add_message(self.user_id, "user", user_text)

            # Get conversation history
            history = await self.memory.get_history(
                self.user_id, self.config.memory.max_history
            )

            # Display thinking status
            await self.display_status_message("Thinking...", emoji="🤔")

            # Get AI response
            response = await self.agent.chat(history)

            # Clear thinking status
            chat_log = self.query_one("#chat_log", RichLog)
            # Remove last status message (thinking)
            # Note: RichLog doesn't support removing, so we just continue

            # Parse for cron jobs
            cron_jobs, cron_errors = self.agent.parse_cron_blocks(response)

            # Show validation errors if any
            if cron_errors:
                for error in cron_errors:
                    await self.display_status_message(f"Cron error: {error}", emoji="⚠️")

            # Create valid jobs
            for job in cron_jobs:
                job_id = await self.scheduler.add_job(
                    self.user_id, job["schedule"], job["task"], job["message"]
                )
                await self.display_status_message(
                    f"Scheduled job #{job_id}: {job['task']} ({job['schedule']})",
                    emoji="✅",
                )

            # Parse for file saves
            save_blocks = self.agent.parse_save_blocks(response)
            for block in save_blocks:
                filepath = await self.workspace.save_file(
                    block["filename"], block["content"]
                )
                await self.display_status_message(
                    f"Saved {filepath.name} to workspace", emoji="💾"
                )

            # Display AI response
            clean = self.agent.clean_response(response)
            if clean:
                await self.display_message("assistant", clean)

            # Store assistant response
            await self.memory.add_message(self.user_id, "assistant", response)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self.display_status_message(f"Error: {e}", emoji="❌")

        finally:
            self.processing = False

    async def display_message(self, role: str, content: str):
        """Display a message in the chat log."""
        title = "You" if role == "user" else "PiLobster 🦞"
        border_style = "blue" if role == "user" else "magenta"

        await self.display_message_panel(title, content, border_style)

        # Increment message count to prevent duplicates from sync
        self.last_message_count += 1

    async def display_status_message(self, message: str, emoji: str = "ℹ️"):
        """Display a system status message."""
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(Text(f"{emoji}  {message}", style="dim italic"), shrink=True)

    async def check_for_new_messages(self):
        """Check for new messages from Telegram and display them.

        This allows TUI to show messages sent via Telegram in real-time.
        """
        if self.processing:
            return  # Don't check while processing our own message

        try:
            # Get current message count
            history = await self.memory.get_history(self.user_id, limit=100)
            current_count = len(history)

            # If there are new messages, display them
            if current_count > self.last_message_count:
                # Get only the new messages
                new_messages = history[self.last_message_count:]
                for msg in new_messages:
                    role = "You" if msg["role"] == "user" else "PiLobster 🦞"
                    border_style = "blue" if msg["role"] == "user" else "magenta"
                    content = msg["content"]

                    # Clean response for display
                    if msg["role"] == "assistant":
                        content = self.agent.clean_response(content)

                    await self.display_message_panel(role, content, border_style)

                self.last_message_count = current_count
        except Exception as e:
            logger.error(f"Error checking for new messages: {e}")

    async def display_message_panel(self, role: str, content: str, border_style: str):
        """Display a message panel in the chat log."""
        chat_log = self.query_one("#chat_log", RichLog)
        panel = Panel(
            Markdown(content) if content.strip() else Text("(empty)", style="dim"),
            title=f"[bold]{role}[/bold]",
            border_style=border_style,
            width=76,  # Fit within 80 char terminal (4 chars for padding/borders)
            expand=False,
        )
        chat_log.write(panel)

    async def handle_scheduler_callback(self, user_id: int, message: str):
        """Callback for scheduler - displays cron message in TUI and processes it.

        This method is called when a cron job triggers.
        """
        # Only process if it's for this TUI's user
        if user_id != self.user_id:
            return

        logger.info(f"Cron job triggered for user {user_id}: {message}")

        # Display notification
        await self.display_status_message(
            f"Scheduled task triggered: {message}", emoji="⏰"
        )

        # Process the message as if the user sent it
        await self.process_message(message)

    def action_clear_history(self) -> None:
        """Clear chat history (Ctrl+L)."""

        async def _clear():
            await self.memory.clear_history(self.user_id)
            chat_log = self.query_one("#chat_log", RichLog)
            chat_log.clear()
            await self.display_status_message("Chat history cleared", emoji="🧹")

        asyncio.create_task(_clear())

    def action_show_status(self) -> None:
        """Show system status (Ctrl+S)."""

        async def _show_status():
            jobs = await self.scheduler.list_jobs(self.user_id)
            files = self.workspace.list_files()

            status_text = f"""**Model:** `{self.config.ollama.model}`
**Host:** `{self.config.ollama.host}`
**Context:** `{self.config.ollama.context_length}` tokens
**Scheduled jobs:** `{len(jobs)}`
**Workspace files:** `{len(files)}`"""

            chat_log = self.query_one("#chat_log", RichLog)
            panel = Panel(
                Markdown(status_text),
                title="[bold]🦞 System Status[/bold]",
                border_style="green",
                width=76,
                expand=False,
            )
            chat_log.write(panel)

        asyncio.create_task(_show_status())

    async def cmd_jobs(self):
        """List scheduled cron jobs."""
        jobs = await self.scheduler.list_jobs(self.user_id)
        if not jobs:
            await self.display_message("assistant", "No scheduled jobs. Ask me to schedule something!")
            return

        lines = ["**Scheduled Jobs**\n"]
        for job in jobs:
            lines.append(f"#{job['id']} — {job['task']}\n  Schedule: `{job['schedule']}`")

        await self.display_message("assistant", "\n".join(lines))

    async def cmd_schedule(self, args: list):
        """Manually create a cron job."""
        # Check if arguments were provided
        if not args or len(args) < 6:
            help_text = """**Usage:** `/schedule <cron> <prompt>`

The prompt will be sent to me when the job triggers.

**Cron format:** `minute hour day month weekday`

**Examples:**
- `/schedule */3 * * * * Tell me a joke`
- `/schedule 0 9 * * * Give me a motivational quote`
- `/schedule 30 14 * * 1-5 Remind me to stand up`

**Common patterns:**
- `*/5 * * * *` — Every 5 minutes
- `0 * * * *` — Every hour
- `0 9 * * *` — Daily at 9am
- `0 9 * * 1` — Every Monday at 9am"""
            await self.display_message("assistant", help_text)
            return

        # Parse cron expression (first 5 args) and message (remaining args)
        cron_parts = args[:5]
        message_parts = args[5:]

        schedule = " ".join(cron_parts)
        message = " ".join(message_parts)

        if not message:
            await self.display_message("assistant", "❌ Prompt cannot be empty.\nUsage: `/schedule <cron> <prompt>`")
            return

        # Create a task description from the message (truncate if needed)
        task = message[:50] + "..." if len(message) > 50 else message

        # Validate and create the job
        try:
            job_id = await self.scheduler.add_job(self.user_id, schedule, task, message)
            result = f"✅ Scheduled job #{job_id}: {task}\nSchedule: `{schedule}`\nMessage: {message}"
            await self.display_message("assistant", result)
        except ValueError as e:
            error = f"❌ Invalid cron expression: {e}\n\nCron format: `minute hour day month weekday`\nExample: `*/3 * * * *` (every 3 minutes)"
            await self.display_message("assistant", error)
        except Exception as e:
            await self.display_message("assistant", f"❌ Error creating job: {e}")

    async def cmd_cancel(self, args: list):
        """Cancel a scheduled job by ID."""
        if not args:
            await self.display_message("assistant", "Usage: `/cancel <job_id>`")
            return

        try:
            job_id = int(args[0])
        except ValueError:
            await self.display_message("assistant", "Job ID must be a number.")
            return

        success = await self.scheduler.cancel_job(job_id)
        if success:
            await self.display_message("assistant", f"✅ Cancelled job #{job_id}")
        else:
            await self.display_message("assistant", f"Job #{job_id} not found.")

    async def cmd_workspace(self):
        """List files in workspace."""
        files = self.workspace.list_files()
        if not files:
            await self.display_message("assistant", "Workspace is empty. Ask me to write some code!")
            return

        lines = ["**Workspace Files**\n"]
        for f in files:
            size_kb = f["size"] / 1024
            lines.append(f"`{f['name']}` ({size_kb:.1f} KB)")

        await self.display_message("assistant", "\n".join(lines))

    async def cmd_save(self, args: list):
        """Manually save code from last response."""
        # Check if filename was provided
        if not args:
            await self.display_message(
                "assistant",
                "**Usage:** `/save filename.py`\n\nThis will save the last code block from my response."
            )
            return

        filename = args[0]

        # Get recent history to find the last assistant message
        history = await self.memory.get_history(self.user_id, limit=10)

        # Find the most recent assistant message with code
        last_code = None
        for msg in reversed(history):  # Start from most recent
            if msg["role"] == "assistant":
                code_blocks = self.agent.extract_code_blocks(msg["content"])
                if code_blocks:
                    last_code = code_blocks[0]["content"]
                    break

        if not last_code:
            await self.display_message(
                "assistant",
                "❌ No code blocks found in recent conversation. Ask me to write some code first!"
            )
            return

        # Save the code
        try:
            filepath = await self.workspace.save_file(filename, last_code)
            await self.display_message("assistant", f"💾 Saved `{filepath.name}` to workspace")
        except Exception as e:
            await self.display_message("assistant", f"❌ Error saving file: {e}")
