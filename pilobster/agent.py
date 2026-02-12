"""Ollama agent — handles communication with the local LLM."""

import re
import json
import logging
from typing import List
from ollama import AsyncClient

from .config import OllamaConfig

logger = logging.getLogger("pilobster.agent")


class Agent:
    """Talks to a local Ollama model and parses structured responses."""

    def __init__(self, config: OllamaConfig, system_prompt: str):
        self.config = config
        self.system_prompt = system_prompt
        self.client = AsyncClient(host=config.host)

    async def warm_up(self):
        """Pre-load the model so it's ready for fast responses."""
        logger.info(f"Warming up model: {self.config.model}")
        try:
            # Try with full parameters first (standard Ollama)
            await self.client.chat(
                model=self.config.model,
                messages=[{"role": "user", "content": "hi"}],
                keep_alive=self.config.keep_alive,
                options={"num_ctx": self.config.context_length},
            )
            logger.info("Model loaded and ready")
        except Exception as e:
            # Fallback for non-standard Ollama implementations (e.g., Hailo AI HAT+2)
            logger.warning(f"Standard warm-up failed, trying minimal request: {e}")
            try:
                await self.client.chat(
                    model=self.config.model,
                    messages=[{"role": "user", "content": "hi"}],
                )
                logger.info("Model loaded and ready (minimal mode)")
            except Exception as e2:
                logger.error(f"Failed to warm up model: {e2}")
                logger.warning("Continuing without warm-up - first request may be slow")
                # Don't raise - allow bot to continue

    async def chat(self, messages: List[dict]) -> str:
        """Send a conversation to the model and return the response text."""
        full_messages = [
            {"role": "system", "content": self.system_prompt},
            *messages,
        ]

        try:
            # Try with full parameters first
            response = await self.client.chat(
                model=self.config.model,
                messages=full_messages,
                keep_alive=self.config.keep_alive,
                options={
                    "num_ctx": self.config.context_length,
                    "temperature": self.config.temperature,
                },
            )
            return response["message"]["content"]
        except Exception as e:
            # Fallback for non-standard implementations
            logger.warning(f"Standard chat failed, trying minimal request: {e}")
            try:
                response = await self.client.chat(
                    model=self.config.model,
                    messages=full_messages,
                )
                return response["message"]["content"]
            except Exception as e2:
                logger.error(f"Ollama chat error: {e2}")
                return f"Sorry, I had trouble thinking about that. Error: {e2}"

    @staticmethod
    def parse_cron_blocks(text: str) -> List[dict]:
        """Extract ```cron ... ``` blocks from the response.

        Expected format:
            ```cron
            {"schedule": "0 9 * * *", "task": "...", "message": "..."}
            ```
        """
        pattern = r"```cron\s*\n(.*?)\n\s*```"
        matches = re.findall(pattern, text, re.DOTALL)
        jobs = []
        for match in matches:
            try:
                job = json.loads(match.strip())
                if all(k in job for k in ("schedule", "task", "message")):
                    jobs.append(job)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse cron block: {match}")
        return jobs

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
        """Remove cron and save blocks from the response for display."""
        text = re.sub(r"```cron\s*\n.*?\n\s*```", "", text, flags=re.DOTALL)
        text = re.sub(r"```save:\S+\s*\n.*?\n\s*```", "", text, flags=re.DOTALL)
        return text.strip()
