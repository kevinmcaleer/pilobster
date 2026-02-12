"""Persistent memory using SQLite for conversation history and cron jobs."""

import aiosqlite
import json
from datetime import datetime
from pathlib import Path


class Memory:
    """SQLite-backed storage for conversations, cron jobs, and notes."""

    def __init__(self, db_path: str = "./pilobster.db"):
        self.db_path = db_path
        self.db: aiosqlite.Connection | None = None

    async def connect(self):
        """Initialise the database and create tables."""
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cron_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                schedule TEXT NOT NULL,
                task TEXT NOT NULL,
                message TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS workspace_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self.db.commit()

    async def close(self):
        """Close the database connection."""
        if self.db:
            await self.db.close()

    # --- Conversation History ---

    async def add_message(self, user_id: int, role: str, content: str):
        """Store a conversation message."""
        await self.db.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await self.db.commit()

    async def get_history(self, user_id: int, limit: int = 50) -> list[dict]:
        """Retrieve recent conversation history for a user."""
        cursor = await self.db.execute(
            "SELECT role, content FROM conversations "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        # Reverse so oldest messages come first
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

    async def clear_history(self, user_id: int):
        """Clear conversation history for a user."""
        await self.db.execute(
            "DELETE FROM conversations WHERE user_id = ?", (user_id,)
        )
        await self.db.commit()

    # --- Cron Jobs ---

    async def add_cron_job(
        self, user_id: int, schedule: str, task: str, message: str
    ) -> int:
        """Add a new cron job. Returns the job ID."""
        cursor = await self.db.execute(
            "INSERT INTO cron_jobs (user_id, schedule, task, message) "
            "VALUES (?, ?, ?, ?)",
            (user_id, schedule, task, message),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_cron_jobs(self, user_id: int | None = None) -> list[dict]:
        """Get all cron jobs, optionally filtered by user."""
        if user_id:
            cursor = await self.db.execute(
                "SELECT id, user_id, schedule, task, message, enabled "
                "FROM cron_jobs WHERE user_id = ? AND enabled = 1",
                (user_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT id, user_id, schedule, task, message, enabled "
                "FROM cron_jobs WHERE enabled = 1"
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "schedule": row[2],
                "task": row[3],
                "message": row[4],
                "enabled": bool(row[5]),
            }
            for row in rows
        ]

    async def disable_cron_job(self, job_id: int) -> bool:
        """Disable a cron job. Returns True if found."""
        cursor = await self.db.execute(
            "UPDATE cron_jobs SET enabled = 0 WHERE id = ?", (job_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Workspace Files ---

    async def log_file(self, filename: str, description: str = ""):
        """Log a file created in the workspace."""
        await self.db.execute(
            "INSERT INTO workspace_files (filename, description) VALUES (?, ?)",
            (filename, description),
        )
        await self.db.commit()

    async def get_workspace_files(self) -> list[dict]:
        """List all files logged in the workspace."""
        cursor = await self.db.execute(
            "SELECT filename, description, created_at FROM workspace_files "
            "ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"filename": row[0], "description": row[1], "created_at": row[2]}
            for row in rows
        ]
