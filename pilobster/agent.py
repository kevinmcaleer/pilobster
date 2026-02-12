"""Ollama agent — handles communication with the local LLM."""

import re
import json
import logging
from typing import List
from pathlib import Path
import httpx

from .config import OllamaConfig

logger = logging.getLogger("pilobster.agent")

# Memory file settings
MEMORY_FILE = Path("memory.md")
MAX_MEMORY_LINES = 100  # Warn if memory exceeds this many lines


class Agent:
    """Talks to a local Ollama model and parses structured responses."""

    def __init__(self, config: OllamaConfig, system_prompt: str):
        self.config = config
        self.base_prompt = system_prompt
        self.memory_content = self._load_memory()
        self.system_prompt = self._build_full_prompt()
        self.http_client = httpx.AsyncClient(timeout=120.0)

    def _load_memory(self) -> str:
        """Load memory.md file if it exists."""
        if MEMORY_FILE.exists():
            try:
                content = MEMORY_FILE.read_text(encoding="utf-8").strip()
                if content:
                    logger.info(f"Loaded {len(content.splitlines())} lines from memory.md")
                    return content
            except Exception as e:
                logger.warning(f"Failed to load memory.md: {e}")
        return ""

    def _build_full_prompt(self) -> str:
        """Build the full system prompt including memory."""
        if self.memory_content:
            return f"{self.base_prompt}\n\n## Personal Memory\nThese are important facts to remember about the user:\n{self.memory_content}"
        return self.base_prompt

    def check_memory_size(self) -> tuple[bool, int]:
        """Check if memory is getting too large.

        Returns: (is_large, line_count)
        """
        lines = len(self.memory_content.splitlines()) if self.memory_content else 0
        return lines > MAX_MEMORY_LINES, lines

    async def save_to_memory(self, fact: str) -> bool:
        """Append a fact to memory.md.

        Returns True if saved successfully.
        """
        try:
            # Check if fact already exists
            if self.memory_content and fact.strip() in self.memory_content:
                logger.debug(f"Fact already in memory: {fact}")
                return False

            # Append to file
            with open(MEMORY_FILE, "a", encoding="utf-8") as f:
                if MEMORY_FILE.stat().st_size > 0:
                    f.write("\n")
                f.write(f"- {fact.strip()}\n")

            # Reload memory
            self.memory_content = self._load_memory()
            self.system_prompt = self._build_full_prompt()

            logger.info(f"Saved to memory: {fact}")
            return True
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            return False

    def clear_memory(self) -> bool:
        """Clear all memory.

        Returns True if cleared successfully.
        """
        try:
            if MEMORY_FILE.exists():
                MEMORY_FILE.unlink()
            self.memory_content = ""
            self.system_prompt = self._build_full_prompt()
            logger.info("Memory cleared")
            return True
        except Exception as e:
            logger.error(f"Failed to clear memory: {e}")
            return False

    async def warm_up(self):
        """Pre-load the model so it's ready for fast responses."""
        logger.info(f"Warming up model: {self.config.model}")
        try:
            # Send a simple warm-up request
            await self._chat_request([{"role": "user", "content": "hi"}])
            logger.info("Model loaded and ready")
        except Exception as e:
            logger.warning(f"Warm-up failed, continuing anyway: {e}")
            # Don't raise - allow bot to continue

    async def _chat_request(self, messages: List[dict]) -> str:
        """Low-level chat request using httpx for better Hailo compatibility."""
        url = f"{self.config.host}/api/chat"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,  # Non-streaming for simplicity
        }

        try:
            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]
        except httpx.ConnectError as e:
            raise Exception(f"Cannot connect to Ollama at {self.config.host}. Is Ollama running? ({e})")
        except httpx.HTTPStatusError as e:
            raise Exception(f"Ollama returned error {e.response.status_code}: {e.response.text}")
        except KeyError as e:
            raise Exception(f"Unexpected response format from Ollama (missing {e})")
        except Exception as e:
            raise Exception(f"Ollama request failed: {type(e).__name__}: {e}")

    async def chat(self, messages: List[dict]) -> str:
        """Send a conversation to the model and return the response text."""
        full_messages = [
            {"role": "system", "content": self.system_prompt},
            *messages,
        ]

        try:
            return await self._chat_request(full_messages)
        except Exception as e:
            logger.error(f"Ollama chat error: {e}")
            return f"Sorry, I had trouble thinking about that. Error: {e}"

    @staticmethod
    def parse_cron_blocks(text: str) -> tuple[List[dict], List[str]]:
        """Extract ```cron ... ``` blocks from the response.

        Expected format:
            ```cron
            {"schedule": "0 9 * * *", "task": "...", "message": "..."}
            ```

        Returns: (valid_jobs, errors)
        """
        pattern = r"```cron\s*\n(.*?)\n\s*```"
        matches = re.findall(pattern, text, re.DOTALL)
        jobs = []
        errors = []

        for match in matches:
            try:
                job = json.loads(match.strip())

                # Validate required fields
                missing = [k for k in ("schedule", "task", "message") if k not in job]
                if missing:
                    errors.append(f"Missing required fields: {', '.join(missing)}")
                    continue

                # Validate cron format (should have 5 fields)
                schedule_parts = job["schedule"].split()
                if len(schedule_parts) != 5:
                    errors.append(f"Invalid cron format '{job['schedule']}' - needs 5 fields (minute hour day month weekday)")
                    continue

                jobs.append(job)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse cron block: {match}")
                errors.append(f"Invalid JSON in cron block")

        return jobs, errors

    @staticmethod
    def parse_save_blocks(text: str) -> List[dict]:
        """Extract ```save:filename ... ``` blocks from the response.

        Expected format:
            ```save:hello.py
            print("hello world")
            ```
        """
        pattern = r"```save:(\S+)\s*\n(.*?)\n\s*```"
        matches = re.findall(pattern, text, re.DOTALL)
        return [{"filename": m[0], "content": m[1]} for m in matches]

    @staticmethod
    def parse_memory_blocks(text: str) -> List[str]:
        """Extract ```memory ... ``` blocks from the response.

        Expected format:
            ```memory
            User is a YouTuber
            ```

        Returns: List of facts to remember
        """
        pattern = r"```memory\s*\n(.*?)\n\s*```"
        matches = re.findall(pattern, text, re.DOTALL)
        return [m.strip() for m in matches if m.strip()]

    @staticmethod
    def extract_code_blocks(text: str) -> List[dict]:
        """Extract all code blocks from text (```language ... ```).

        Returns a list of dicts with 'language' and 'content' keys.
        """
        pattern = r"```(\w+)?\s*\n(.*?)\n\s*```"
        matches = re.findall(pattern, text, re.DOTALL)
        return [
            {"language": m[0] or "text", "content": m[1].strip()}
            for m in matches
        ]

    @staticmethod
    def clean_response(text: str) -> str:
        """Remove cron, save, and memory blocks from the response for display."""
        text = re.sub(r"```cron\s*\n.*?\n\s*```", "", text, flags=re.DOTALL)
        text = re.sub(r"```save:\S+\s*\n.*?\n\s*```", "", text, flags=re.DOTALL)
        text = re.sub(r"```memory\s*\n.*?\n\s*```", "", text, flags=re.DOTALL)
        return text.strip()
