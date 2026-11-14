"""In-process schedule for the nightly optimization job.

The job is ``deepfix-optimize-workspace``. It calls the same function as Run now.
It runs inside the API process, not as an agent-side cron job.

Timing belongs to APScheduler 3.x (``BackgroundScheduler`` + ``CronTrigger``),
not to a hand-written sleep loop. The API is a synchronous FastAPI app whose
other background work (``FeedbackWorker``) is already a thread and whose
schedule routes are plain ``def`` functions, so the background variant fits: it
needs no running event loop, whereas ``AsyncIOScheduler`` would need one that
spans the app's lifespan.

The SQLite row stays the source of truth. Nothing new is persisted: the
APScheduler job store is the in-memory default, and the live job is rebuilt from
the stored row at every start, so a restart and the Admin UI's ``next_run_at``
never disagree.

What the job options mean:

* ``coalesce=True`` — several fire times missed while the process could not run
  jobs collapse into one run instead of a burst.
* ``max_instances=1`` — one fire time is never executed by two worker threads at
  once. ``RunInProgress`` inside ``start_optimization_run`` remains the real
  serialisation for optimization runs, which outlive this job.
* ``misfire_grace_time=MISFIRE_GRACE_SECONDS`` — a fire time is honoured for up
  to five minutes after it was due, so a run delayed by a brief stall still
  happens; a longer delay is dropped (APScheduler reports ``EVENT_JOB_MISSED``)
  and the next fire is the next cron time. **A schedule missed while the process
  was down is not caught up at all**: the job is armed from now, so the first
  fire after a restart is the next cron time. That is what the previous loop did
  and what ``next_run_at`` shows.

Edits apply immediately: ``update`` persists the row and then calls
``reschedule_job``, ``pause_job`` or ``resume_job``. Each of those wakes the
scheduler, so a changed, earlier, paused or resumed schedule takes effect at
once instead of at the old next-run time.

One process, one scheduler. The Dockerfile runs a single uvicorn worker, which
the SQLite store and the index writer already assume. ``start`` is idempotent per
instance, and a second scheduler for the same database inside one process logs a
warning: ``max_instances`` and ``RunInProgress`` then turn what would be a double
run into a logged refusal.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from deepfix_rag.config.settings import LOGGER
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.optimization import (
    AgentUnavailable,
    OptimizationSettings,
    RunInProgress,
    agent_status,
    start_optimization_run,
)
from deepfix_rag.storage import connect

JOB_NAME = "deepfix-optimize-workspace"
OPTIMIZATION_JOB_ID = JOB_NAME
PRUNE_JOB_ID = "admin-chat-audit-prune"
DEFAULT_CRON = "0 2 * * *"
DEFAULT_TIMEZONE = "Europe/Berlin"
# How late a fire time may still run. Five minutes absorbs a short stall of the
# process and is short enough that a nightly job never surfaces at an arbitrary
# hour after a long one. A constant, not a setting: it says what "the nightly
# run" means rather than expressing a deployment's preference.
MISFIRE_GRACE_SECONDS = 300
# The audit prune is hourly (PRD D-6): often enough that a row never outlives its
# retention window by much, cheap enough to be one DELETE.
PRUNE_INTERVAL_HOURS = 1
_INVALID = "Enter five cron fields, such as 0 2 * * *."
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_armed_databases: set[str] = set()
_armed_lock = threading.Lock()


class InvalidSchedule(Exception):
    """The cron expression is not five valid fields."""

    def __init__(self) -> None:
        super().__init__(_INVALID)


@dataclass(frozen=True)
class CompiledCron:
    """One five-field cron expression, compiled once."""

    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    day_star: bool
    weekday_star: bool
    source: str


@dataclass(frozen=True)
class StoredSchedule:
    """The schedule row, which is the source of truth for the live job."""

    cron: str
    enabled: bool


@dataclass(frozen=True)
class ScheduleView:
    """The schedule the admin screen shows."""

    cron: str
    timezone: str
    enabled: bool
    next_run_at: str | None
    agent: Literal["hermes"]
    agent_available: bool
    agent_unavailable_message: str | None


def compile_cron(expression: str) -> CompiledCron:
    """Parse a five-field cron expression. Names and seconds are rejected."""
    fields = expression.strip().split()
    if len(fields) != 5:
        raise InvalidSchedule()
    compiled = tuple(
        _compile_field(field, low, high)
        for field, (low, high) in zip(fields, _RANGES, strict=True)
    )
    return CompiledCron(
        minute=compiled[0],
        hour=compiled[1],
        day=compiled[2],
        month=compiled[3],
        weekday=compiled[4],
        day_star=fields[2] == "*",
        weekday_star=fields[4] == "*",
        source=" ".join(fields),
    )


def cron_matches(compiled: CompiledCron, local: datetime) -> bool:
    """True when this local time is a fire time. Sunday is 0 or 7."""
    if local.minute not in compiled.minute or local.hour not in compiled.hour:
        return False
    if local.month not in compiled.month:
        return False
    weekday = (local.weekday() + 1) % 7
    day_ok = local.day in compiled.day
    weekday_ok = weekday in compiled.weekday
    if compiled.day_star and compiled.weekday_star:
        return True
    if compiled.day_star:
        return weekday_ok
    if compiled.weekday_star:
        return day_ok
    return day_ok or weekday_ok


def next_run(
    expression: str,
    *,
    after: datetime,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    """Return the next fire time strictly after ``after``, in UTC.

    Answered by the same trigger the job is armed with, so the time the Admin UI
    shows and the time the job waits for cannot drift apart. A time that never
    occurs (``0 0 31 2 *``) is rejected, as it always was, rather than being
    handed to a trigger that would search for it forever.
    """
    trigger = cron_trigger(compile_cron(expression), timezone_name=timezone_name)
    upcoming = _next_fire_after(trigger, after)
    if upcoming is None:
        raise InvalidSchedule()
    return upcoming


def _next_fire_after(trigger: BaseTrigger, moment: datetime) -> datetime | None:
    """The trigger's next fire time strictly after ``moment``.

    ``get_next_fire_time(None, now)`` may return ``now`` itself when ``now`` is
    exactly a fire time, and it has no horizon, so a trigger that can never fire
    would be searched for without end. The second call covers the first, and the
    year bound turns the second into ``None``.
    """
    upcoming = trigger.get_next_fire_time(None, moment)
    if upcoming is not None and upcoming <= moment:
        upcoming = trigger.get_next_fire_time(upcoming, moment)
    if upcoming is None or upcoming.year > moment.year + 4:
        return None
    return upcoming.astimezone(timezone.utc)


def cron_trigger(compiled: CompiledCron, *, timezone_name: str) -> BaseTrigger:
    """Build the APScheduler trigger for a compiled five-field cron.

    Fields go in as explicit value lists, so ``*/15`` means here exactly what it
    means in ``cron_matches``. APScheduler numbers weekdays Monday=0 while cron
    numbers them Sunday=0, hence the shift. When both the day of the month and
    the day of the week are restricted, cron fires on either one and APScheduler
    3.x ANDs every field, so that case is an ``OrTrigger`` of the two.
    """
    common: dict[str, object] = {
        "minute": _values(compiled.minute),
        "hour": _values(compiled.hour),
        "month": _values(compiled.month),
        "timezone": ZoneInfo(timezone_name),
    }
    weekdays = (
        "*" if compiled.weekday_star else _values({(value + 6) % 7 for value in compiled.weekday})
    )
    days = "*" if compiled.day_star else _values(compiled.day)
    if compiled.day_star or compiled.weekday_star:
        return CronTrigger(day=days, day_of_week=weekdays, **common)
    return OrTrigger(
        [
            CronTrigger(day=days, day_of_week="*", **common),
            CronTrigger(day="*", day_of_week=weekdays, **common),
        ]
    )


def _values(values: frozenset[int] | set[int]) -> str:
    return ",".join(str(value) for value in sorted(values))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OptimizationScheduler:
    """One APScheduler instance that starts the optimization job on its cron."""

    def __init__(
        self,
        db_path: Path,
        workspace: Path,
        *,
        options_for: Callable[[], OptimizationSettings],
        coordinator_for: Callable[[], IndexCoordinator | None],
        timezone_name: str = DEFAULT_TIMEZONE,
        prune_audit: Callable[[], int] | None = None,
        scheduler_for: Callable[[], BackgroundScheduler] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = db_path
        self.workspace = workspace
        self._options_for = options_for
        self._coordinator_for = coordinator_for
        self._timezone = timezone_name
        self._prune_audit = prune_audit
        self._now = clock or _utc_now
        # The scheduler itself is the only seam: the tests pass one that can be
        # ticked on a fake clock, production gets the threaded background one.
        self._scheduler = (
            scheduler_for()
            if scheduler_for is not None
            else BackgroundScheduler(timezone=timezone.utc)
        )
        self._lock = threading.RLock()
        self._started = False
        self._armed_cron: str | None = None
        self._last_minute: str | None = None

    def start(self) -> None:
        """Load the stored schedule, arm the jobs, and start the scheduler."""
        with self._lock:
            if self._started:
                return
            self._arm(self._load())
            if self._prune_audit is not None:
                self._arm_prune()
            self._warn_if_armed_twice()
            self._scheduler.start()
            self._started = True

    def stop(self) -> None:
        """Stop the scheduler and join its thread. A run already started keeps going."""
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._armed_cron = None
        # Shutdown joins the scheduler thread, which may be waiting on this lock
        # inside run_due, so it must not be called while holding it.
        self._scheduler.shutdown(wait=False)
        with _armed_lock:
            _armed_databases.discard(str(self.db_path))

    @property
    def running(self) -> bool:
        """Whether the scheduler is started and its loop is alive."""
        return self._started and self._scheduler.running

    @property
    def job(self) -> Job | None:
        """The live optimization job, for inspection. ``None`` before ``start``."""
        return self._scheduler.get_job(OPTIMIZATION_JOB_ID)

    def view(self) -> ScheduleView:
        """Return the stored schedule and whether a run can start."""
        stored = self._read()
        options = self._options_for()
        available, message = agent_status(options)
        agent: Literal["hermes"] = "hermes"
        return ScheduleView(
            cron=stored.cron,
            timezone=self._timezone,
            enabled=stored.enabled,
            next_run_at=self._next_run_at(stored),
            agent=agent,
            agent_available=available,
            agent_unavailable_message=message,
        )

    def update(self, cron: str, enabled: bool) -> ScheduleView:
        """Store a new schedule and apply it to the live job at once.

        An invalid expression raises before either the row or the job changes.
        """
        compiled = compile_cron(cron)
        self._persist(compiled.source, enabled)
        self._apply(compiled.source, enabled)
        return self.view()

    def run_due(self, now: datetime | None = None) -> bool:
        """Start one run when the stored schedule says this moment is a fire time.

        Called by the job with no argument, the fire time was already chosen by
        the live trigger, so this only re-reads the row, makes sure the job still
        matches it, and starts the run. ``now`` is for tests: it turns the due
        check into a pure comparison against the cron, with no waiting.
        """
        stored = self._read()
        if not stored.enabled:
            # A pause written without going through update() — another process,
            # or a hand-edited row — still stops this fire.
            self._apply(stored.cron, False)
            return False
        if now is None and self._armed_cron != stored.cron:
            # The row changed out of band. Re-arm from it and drop this fire,
            # which belonged to the previous cron; the new trigger owns the next.
            LOGGER.info("Optimization schedule changed out of band; re-armed")
            self._apply(stored.cron, True)
            return False
        moment = self._now() if now is None else now.astimezone(timezone.utc)
        local = moment.astimezone(ZoneInfo(self._timezone))
        if now is not None and not cron_matches(compile_cron(stored.cron), local):
            return False
        with self._lock:
            minute_key = local.strftime("%Y%m%d%H%M")
            if minute_key == self._last_minute:
                return False
            self._last_minute = minute_key
        self._start_run()
        return True

    def _start_run(self) -> None:
        try:
            start_optimization_run(
                self.db_path,
                self.workspace,
                trigger="cron",
                options=self._options_for(),
                coordinator=self._coordinator_for(),
            )
        except (RunInProgress, AgentUnavailable):
            LOGGER.info("Scheduled optimization did not start")
        except Exception:
            LOGGER.exception("Scheduled optimization failed")

    def _fire(self) -> None:
        """The optimization job's body. A failure here must not stop the scheduler."""
        try:
            self.run_due()
        except Exception:
            LOGGER.exception("Scheduled optimization failed")

    def _prune(self) -> None:
        """The audit prune's body, when a callable was injected."""
        prune = self._prune_audit
        if prune is None:
            return
        try:
            prune()
        except Exception:
            LOGGER.exception("Chat audit prune failed")

    def _arm(self, stored: StoredSchedule) -> None:
        """Add the optimization job, paused when the stored schedule is paused."""
        trigger = self._cron_trigger(stored.cron)
        self._scheduler.add_job(
            self._fire,
            trigger,
            id=OPTIMIZATION_JOB_ID,
            name=JOB_NAME,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            next_run_time=None if not stored.enabled else self._first_fire(trigger),
            replace_existing=True,
        )
        self._armed_cron = stored.cron

    def _arm_prune(self) -> None:
        """Add the hourly audit prune to the same scheduler.

        It fires once at startup and then hourly, so a process that was down for
        a week prunes on its first tick rather than an hour later. A late prune
        is harmless and idempotent, so its misfire grace is unlimited while
        ``coalesce`` still collapses a backlog into one call.
        """
        self._scheduler.add_job(
            self._prune,
            IntervalTrigger(hours=PRUNE_INTERVAL_HOURS, start_date=self._now()),
            id=PRUNE_JOB_ID,
            name=PRUNE_JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            replace_existing=True,
        )

    def _cron_trigger(self, cron: str) -> BaseTrigger:
        return cron_trigger(compile_cron(cron), timezone_name=self._timezone)

    def _first_fire(self, trigger: BaseTrigger) -> datetime:
        """The next fire time for a job armed from now, in UTC.

        Asked of the very trigger the job will use, so the time the Admin UI
        shows and the time the job waits for cannot drift apart.
        """
        upcoming = trigger.get_next_fire_time(None, self._now())
        if upcoming is None:
            raise InvalidSchedule()
        return upcoming.astimezone(timezone.utc)

    def _apply(self, cron: str, enabled: bool) -> None:
        """Point the live job at ``cron`` and pause or resume it. Applies at once."""
        with self._lock:
            if not self._started:
                self._armed_cron = None
                return
            if self._scheduler.get_job(OPTIMIZATION_JOB_ID) is None:
                self._arm(StoredSchedule(cron=cron, enabled=enabled))
                return
            if self._armed_cron != cron:
                # reschedule_job recomputes the next fire time and wakes the
                # scheduler, so the new trigger is live immediately.
                self._scheduler.reschedule_job(
                    OPTIMIZATION_JOB_ID,
                    trigger=self._cron_trigger(cron),
                )
                self._armed_cron = cron
            job = self._scheduler.get_job(OPTIMIZATION_JOB_ID)
            paused = job is None or job.next_run_time is None
            # resume_job recomputes the next fire time from the trigger, which
            # would skip a fire time that is only seconds away, so it runs only
            # when the job really is paused.
            if enabled and paused:
                self._scheduler.resume_job(OPTIMIZATION_JOB_ID)
            elif not enabled and not paused:
                self._scheduler.pause_job(OPTIMIZATION_JOB_ID)

    def _next_run_at(self, stored: StoredSchedule) -> str | None:
        if not stored.enabled:
            return None
        if self._started:
            job = self._scheduler.get_job(OPTIMIZATION_JOB_ID)
            armed = getattr(job, "next_run_time", None) if job is not None else None
            if armed is not None:
                return armed.astimezone(timezone.utc).isoformat()
        return self._first_fire(self._cron_trigger(stored.cron)).isoformat()

    def _warn_if_armed_twice(self) -> None:
        key = str(self.db_path)
        with _armed_lock:
            already = key in _armed_databases
            _armed_databases.add(key)
        if already:
            LOGGER.warning(
                "A second optimization scheduler is starting for %s in this process. "
                "Run one API worker.",
                key,
            )

    def _load(self) -> StoredSchedule:
        """Create and seed the schedule table, then read it back."""
        with connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            row = conn.execute(
                "SELECT cron FROM optimization_schedule WHERE job_name = ?",
                (JOB_NAME,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO optimization_schedule (job_name, cron, timezone, enabled)
                    VALUES (?, ?, ?, 1)
                    """,
                    (JOB_NAME, DEFAULT_CRON, self._timezone),
                )
        return self._read()

    def _read(self) -> StoredSchedule:
        """Read the schedule row. A missing row reads as the default, not as a pause."""
        with connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            row = conn.execute(
                "SELECT cron, enabled FROM optimization_schedule WHERE job_name = ?",
                (JOB_NAME,),
            ).fetchone()
        if row is None:
            return StoredSchedule(cron=DEFAULT_CRON, enabled=True)
        try:
            cron = compile_cron(str(row["cron"])).source
        except InvalidSchedule:
            LOGGER.warning("Stored optimization cron is invalid; not running it")
            return StoredSchedule(cron=DEFAULT_CRON, enabled=False)
        return StoredSchedule(cron=cron, enabled=bool(row["enabled"]))

    def _persist(self, cron: str, enabled: bool) -> None:
        with connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(
                """
                INSERT INTO optimization_schedule (job_name, cron, timezone, enabled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    cron = excluded.cron,
                    timezone = excluded.timezone,
                    enabled = excluded.enabled
                """,
                (JOB_NAME, cron, self._timezone, 1 if enabled else 0),
            )


_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS optimization_schedule (
        job_name TEXT PRIMARY KEY,
        cron TEXT NOT NULL,
        timezone TEXT NOT NULL,
        enabled INTEGER NOT NULL
    )
"""


def _compile_field(field: str, low: int, high: int) -> frozenset[int]:
    if not field or any(character not in "0123456789*,/-" for character in field):
        raise InvalidSchedule()
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise InvalidSchedule()
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            if not raw_step.isdigit() or int(raw_step) < 1:
                raise InvalidSchedule()
            step = int(raw_step)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, right = base.split("-", 1)
            if not left.isdigit() or not right.isdigit():
                raise InvalidSchedule()
            start, end = int(left), int(right)
            if start > end or start < low or end > high:
                raise InvalidSchedule()
        else:
            if not base.isdigit():
                raise InvalidSchedule()
            number = int(base)
            if number < low or number > high:
                raise InvalidSchedule()
            start = end = number
        for value in range(start, end + 1, step):
            stored = 0 if high == 7 and value == 7 else value
            if stored < low or stored > (6 if high == 7 else high):
                raise InvalidSchedule()
            values.add(stored)
    if not values:
        raise InvalidSchedule()
    return frozenset(values)
