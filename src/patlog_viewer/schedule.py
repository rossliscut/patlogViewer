"""Schedule trigger. It calls the same run function as the manual button."""

from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from patlog_viewer.jobs import RunError, Runner


def tick(runner: Runner) -> dict | None:
    config = runner.db.schedule()
    if not config["enabled"]:
        return None
    try:
        run, _already = runner.start_remote(trigger="schedule", window=config["window"])
    except RunError:
        return None
    return run


def start_scheduler(runner: Runner) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    def remember(event) -> None:
        if event.job_id != "patlog-fetch":
            return
        job = scheduler.get_job("patlog-fetch")
        if job is None or job.next_run_time is None:
            return
        runner.db.set_next_run(_naive(job.next_run_time))

    scheduler.add_listener(remember, EVENT_JOB_EXECUTED)
    scheduler.start()
    apply_schedule(scheduler, runner)
    return scheduler


def next_fire(config: dict, now: datetime | None = None) -> datetime | None:
    """Next local clock time this schedule would run. None when it is off."""
    if not config["enabled"]:
        return None
    moment = (now or datetime.now()).replace(microsecond=0)
    mode = config.get("mode") or "interval"
    if mode == "daily":
        candidate = moment.replace(
            hour=int(config["hour"]),
            minute=int(config["minute"]),
            second=0,
            microsecond=0,
        )
        if candidate <= moment:
            candidate += timedelta(days=1)
        return candidate
    if mode == "hourly":
        candidate = moment.replace(minute=int(config["minute"]), second=0, microsecond=0)
        if candidate <= moment:
            candidate += timedelta(hours=1)
        return candidate
    return moment + timedelta(minutes=int(config["interval_minutes"]))


def _naive(moment: datetime) -> datetime:
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.replace(microsecond=0)


def _stored_clock(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def keep_interval(anchor: datetime, minutes: int, now: datetime) -> datetime:
    """Stay on the saved cadence. A missed tick moves to the next one still ahead."""
    if anchor >= now:
        return anchor
    step = timedelta(minutes=minutes)
    jumps = (now - anchor) // step + 1
    return anchor + jumps * step


def resume_fire(config: dict, now: datetime | None = None) -> datetime | None:
    """Next run after a process start. Interval mode keeps the time stored in SQLite."""
    moment = (now or datetime.now()).replace(microsecond=0)
    mode = config.get("mode") or "interval"
    stored = _stored_clock(config.get("next_run"))
    if mode == "interval" and stored is not None and config.get("enabled"):
        return keep_interval(stored, int(config["interval_minutes"]), moment)
    return next_fire(config, moment)


def apply_schedule(scheduler: BackgroundScheduler, runner: Runner, *, reschedule: bool = False) -> None:
    if scheduler.get_job("patlog-fetch"):
        scheduler.remove_job("patlog-fetch")
    config = runner.db.schedule()
    when = next_fire(config) if reschedule else resume_fire(config)
    if when is None:
        runner.db.set_next_run(None)
        return
    runner.db.set_next_run(when)
    mode = config.get("mode") or "interval"
    if mode == "daily":
        trigger = CronTrigger(hour=int(config["hour"]), minute=int(config["minute"]))
    elif mode == "hourly":
        trigger = CronTrigger(minute=int(config["minute"]))
    else:
        trigger = IntervalTrigger(minutes=int(config["interval_minutes"]))
    scheduler.add_job(
        tick,
        trigger,
        id="patlog-fetch",
        args=[runner],
        next_run_time=when,
        replace_existing=True,
    )
