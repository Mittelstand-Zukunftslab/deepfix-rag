"""In-process schedule for the nightly optimization job.

The job is ``deepfix-optimize-workspace``. It calls the same function as Run now.
It runs inside the API process, not as an agent-side cron job.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

from deepfix_rag.agent_process import AgentUnavailable
from deepfix_rag.config.settings import LOGGER
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.optimization import (
    OptimizationSettings,
    RunInProgress,
    agent_available,
    start_optimization_run,
)
from deepfix_rag.storage import connect

JOB_NAME = "deepfix-optimize-workspace"
DEFAULT_CRON = "0 2 * * *"
DEFAULT_TIMEZONE = "Europe/Berlin"
_INVALID = "Enter five cron fields, such as 0 2 * * *."
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


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
class ScheduleView:
    """The schedule the admin screen shows."""

    cron: str
    timezone: str
    enabled: bool
    next_run_at: str | None
    agent_available: bool


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
    """Return the next fire time strictly after ``after``, in UTC."""
    compiled = compile_cron(expression)
    zone = ZoneInfo(timezone_name)
    local = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if cron_matches(compiled, local):
            return local.astimezone(timezone.utc)
        local += timedelta(minutes=1)
    raise InvalidSchedule()


class OptimizationScheduler:
    """One background thread that starts the optimization job on its cron."""

    def __init__(
        self,
        db_path: Path,
        workspace: Path,
        *,
        options_for: Callable[[], OptimizationSettings],
        coordinator_for: Callable[[], IndexCoordinator | None],
        timezone_name: str = DEFAULT_TIMEZONE,
    ) -> None:
        self.db_path = db_path
        self.workspace = workspace
        self._options_for = options_for
        self._coordinator_for = coordinator_for
        self._timezone = timezone_name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cron = DEFAULT_CRON
        self._compiled = compile_cron(DEFAULT_CRON)
        self._enabled = True
        self._next: datetime | None = None
        self._last_minute: str | None = None

    def start(self) -> None:
        """Load the stored schedule and start the thread."""
        self._load()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=JOB_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread. A run that already started keeps going."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def view(self) -> ScheduleView:
        """Return the schedule and whether a run can start."""
        with self._lock:
            cron = self._cron
            enabled = self._enabled
            nxt = self._next
        return ScheduleView(
            cron=cron,
            timezone=self._timezone,
            enabled=enabled,
            next_run_at=None if not enabled or nxt is None else nxt.isoformat(),
            agent_available=agent_available(self._options_for()),
        )

    def update(self, cron: str, enabled: bool) -> ScheduleView:
        """Store a new schedule. An invalid expression leaves the current one."""
        compiled = compile_cron(cron)
        nxt = (
            None
            if not enabled
            else next_run(
                compiled.source,
                after=datetime.now(timezone.utc),
                timezone_name=self._timezone,
            )
        )
        with self._lock:
            self._cron = compiled.source
            self._compiled = compiled
            self._enabled = enabled
            self._next = nxt
            self._persist()
        self._wake.set()
        return self.view()

    def run_due(self, now: datetime | None = None) -> bool:
        """Start one run when the schedule matches ``now``. A paused job does not."""
        moment = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
        local = moment.astimezone(ZoneInfo(self._timezone))
        minute_key = local.strftime("%Y%m%d%H%M")
        with self._lock:
            if not self._enabled or minute_key == self._last_minute:
                return False
            if now is None:
                due = self._next is not None and moment >= self._next
            else:
                due = cron_matches(self._compiled, local)
            if not due:
                return False
            self._last_minute = minute_key
            cron = self._cron
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
        with self._lock:
            self._next = next_run(cron, after=moment, timezone_name=self._timezone)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                nxt = self._next if self._enabled else None
            if nxt is None:
                delay = 3600.0
            else:
                delay = max(0.0, (nxt - datetime.now(timezone.utc)).total_seconds())
            if self._wake.wait(delay):
                self._wake.clear()
                if self._stop.is_set():
                    return
                continue
            if self._stop.is_set():
                return
            self.run_due()

    def _load(self) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS optimization_schedule (
                    job_name TEXT PRIMARY KEY,
                    cron TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                )
                """
            )
            row = conn.execute(
                "SELECT cron, timezone, enabled FROM optimization_schedule WHERE job_name = ?",
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
                cron = DEFAULT_CRON
                enabled = True
            else:
                cron = str(row["cron"])
                enabled = bool(row["enabled"])
        compiled = compile_cron(cron)
        nxt = (
            None
            if not enabled
            else next_run(cron, after=datetime.now(timezone.utc), timezone_name=self._timezone)
        )
        with self._lock:
            self._cron = compiled.source
            self._compiled = compiled
            self._enabled = enabled
            self._next = nxt

    def _persist(self) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO optimization_schedule (job_name, cron, timezone, enabled)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    cron = excluded.cron,
                    timezone = excluded.timezone,
                    enabled = excluded.enabled
                """,
                (JOB_NAME, self._cron, self._timezone, 1 if self._enabled else 0),
            )


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
