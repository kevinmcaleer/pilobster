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
    }

    #chat_container {
        height: 1fr;
        border: solid $primary;
        background: $surface;
    }

    #chat_log {
        height: 1fr;
        background: $surface;
        border: none;
        padding: 1 2;
    }

    #user_input {
        dock: bottom;
        border: solid $primary;
        height: 3;
        margin: 0 1;
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
                f"""# Welcome to PiLobster! 🦞\n\nI'm your local AI assistant running on **{self.config.ollama.model}**.\n\n**Keyboard Shortcuts:**\n- `Ctrl+L` - Clear chat history\n- `Ctrl+S` - Show system status\n- `Ctrl+C` - Quit\n\nJust start typing to chat with me!"""
            ),
            title="🦞 PiLobster",
            border_style="green",
        )
        chat_log.write(welcome)

        # Load conversation history
        history = await self.memory.get_history(self.user_id, limit=20)
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
                )
                chat_log.write(panel)

        # Focus input
        self.query_one("#user_input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user message submission."""
        if self.processing:
            return

        user_text = event.value.strip()
        if not user_text:
            return

        # Clear input
        event.input.value = ""

        # Process message
        await self.process_message(user_text)

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
        chat_log = self.query_one("#chat_log", RichLog)

        title = "You" if role == "user" else "PiLobster 🦞"
        border_style = "blue" if role == "user" else "magenta"

        panel = Panel(
            Markdown(content) if content.strip() else Text("(empty)", style="dim"),
            title=f"[bold]{title}[/bold]",
            border_style=border_style,
        )
        chat_log.write(panel)

    async def display_status_message(self, message: str, emoji: str = "ℹ️"):
        """Display a system status message."""
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.write(Text(f"{emoji}  {message}", style="dim italic"), shrink=True)

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
            )
            chat_log.write(panel)

        asyncio.create_task(_show_status())
