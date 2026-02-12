"""Configuration loader for PiLobster."""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


@dataclass
class TelegramConfig:
    token: str = ""
    allowed_users: List[int] = field(default_factory=list)


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    model: str = "tinyllama"
    keep_alive: int = -1
    context_length: int = 4096
    temperature: float = 0.7


@dataclass
class WorkspaceConfig:
    path: str = "./workspace"


@dataclass
class SchedulerConfig:
    enabled: bool = True


@dataclass
class MemoryConfig:
    database: str = "./pilobster.db"
    max_history: int = 50


@dataclass
class Config:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    system_prompt: str = "You are PiLobster, a helpful AI assistant."


def load_config(path: str = "config.yaml") -> Config:
    """Load configuration from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to config.yaml and edit it."
        )

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    config = Config()

    if "telegram" in raw:
        config.telegram = TelegramConfig(**raw["telegram"])
    if "ollama" in raw:
        config.ollama = OllamaConfig(**raw["ollama"])
    if "workspace" in raw:
        config.workspace = WorkspaceConfig(**raw["workspace"])
    if "scheduler" in raw:
        config.scheduler = SchedulerConfig(**raw["scheduler"])
    if "memory" in raw:
        config.memory = MemoryConfig(**raw["memory"])
    if "system_prompt" in raw:
        config.system_prompt = raw["system_prompt"]

    return config
