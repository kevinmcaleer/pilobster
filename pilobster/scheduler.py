"""Cron scheduler — runs recurring tasks via APScheduler."""

import logging
from typing import List, Callable, Awaitable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .memory import Memory

logger = logging.getLogger("pilobster.scheduler")


class Scheduler:
    """Manages cron jobs that send messages back to users via Telegram."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.apscheduler = AsyncIOScheduler()
        self._send_callbacks = []

    def set_send_callback(self, callback: Callable[[int, str], Awaitable[None]]):
        """Set the callback function for sending messages.

        The callback should accept (user_id: int, message: str).
        """
        self._send_callbacks = [callback]

    def add_send_callback(self, callback: Callable[[int, str], Awaitable[None]]):
        """Add an additional callback function for sending messages.

        Useful for "both" mode where both TUI and Telegram need notifications.
        """
        if callback not in self._send_callbacks:
            self._send_callbacks.append(callback)

    async def load_jobs(self):
        """Load all enabled cron jobs from the database."""
        jobs = await self.memory.get_cron_jobs()
        for job in jobs:
            self._add_apscheduler_job(job)
        logger.info(f"Loaded {len(jobs)} cron job(s) from database")

    async def add_job(
        self, user_id: int, schedule: str, task: str, message: str
    ) -> int:
        """Add a new cron job and schedule it."""
        job_id = await self.memory.add_cron_job(user_id, schedule, task, message)
        job = {
            "id": job_id,
            "user_id": user_id,
            "schedule": schedule,
            "task": task,
            "message": message,
        }
        self._add_apscheduler_job(job)
        logger.info(f"Added cron job #{job_id}: '{task}' ({schedule})")
        return job_id

    async def cancel_job(self, job_id: int) -> bool:
        """Cancel a cron job by ID."""
        success = await self.memory.disable_cron_job(job_id)
        if success:
            try:
                self.apscheduler.remove_job(f"cron_{job_id}")
            except Exception:
                pass
            logger.info(f"Cancelled cron job #{job_id}")
        return success

    async def list_jobs(self, user_id: int) -> List[dict]:
        """List all active cron jobs for a user."""
        return await self.memory.get_cron_jobs(user_id)

    def _add_apscheduler_job(self, job: dict):
        """Register a job with APScheduler."""
        try:
            parts = job["schedule"].split()
            if len(parts) != 5:
                logger.warning(f"Invalid cron schedule for job #{job['id']}: {job['schedule']}")
                return

            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )

            self.apscheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=f"cron_{job['id']}",
                kwargs={"user_id": job["user_id"], "message": job["message"]},
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Failed to schedule job #{job['id']}: {e}")

    async def _execute_job(self, user_id: int, message: str):
        """Execute a cron job by sending a message to all registered callbacks."""
        if self._send_callbacks:
            for callback in self._send_callbacks:
                try:
                    await callback(user_id, message)
                    logger.debug(f"Cron job sent message to user {user_id} via {callback.__name__}")
                except Exception as e:
                    logger.error(f"Failed to send cron message via {callback.__name__}: {e}")
        else:
            logger.warning("No send callbacks registered — cron message dropped")

    def start(self):
        """Start the APScheduler."""
        self.apscheduler.start()
        logger.info("Scheduler started")

    def stop(self):
        """Stop the APScheduler."""
        self.apscheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
