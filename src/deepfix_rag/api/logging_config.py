"""Console logging for the API process.

uvicorn configures the ``uvicorn`` and ``uvicorn.access`` loggers and nothing
else, and it leaves the root logger without a handler. A record from
``deepfix_rag`` or ``apscheduler`` therefore had nowhere to go: ``docker logs``
of the API showed the request lines and no sign that the nightly optimization
job had been armed, paused, or fired, and APScheduler's own misfire warning was
dropped as well.

``configure_logging`` attaches one stdout handler to the loggers this process
owns and sets their level from ``DEEPFIX_LOG_LEVEL`` (default ``INFO``). It
configures those names only: uvicorn keeps its handlers, the root logger stays
untouched, and every line is printed once. ``propagate`` stays on so a host that
does attach a root handler (pytest's ``caplog``, for one) still receives these
records; the container's root logger has none, so nothing is printed twice.
"""

from collections.abc import Mapping
import logging
from logging.config import dictConfig
import os

LOG_LEVEL_ENV = "DEEPFIX_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
# The levels an operator may ask for. NOTSET is left out on purpose: it hands the
# decision back to a parent logger and would print DEBUG lines instead.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# ``deepfix_rag`` covers every module logger in the package.
# ``DeepFix RAG Eval Runner`` is the shared ``LOGGER`` in ``config.settings``
# that the optimizer, the index writer, and the optimization scheduler log on.
# ``apscheduler`` reports missed fire times and executor errors.
APP_LOGGERS = ("deepfix_rag", "DeepFix RAG Eval Runner", "apscheduler")
CONSOLE_HANDLER = "console"


def normalize_level(value: str | None) -> str:
    """Return a level name the standard library accepts, or the default.

    A typo must not stop the API from booting: an unknown value falls back to
    ``INFO`` and ``configure_logging`` names the variable in a warning instead.
    """
    candidate = (value or "").strip().upper()
    return candidate if candidate in LOG_LEVELS else DEFAULT_LOG_LEVEL


def requested_level(environ: Mapping[str, str] | None = None) -> str | None:
    """The raw ``DEEPFIX_LOG_LEVEL`` value, read from the environment by default."""
    env = os.environ if environ is None else environ
    return env.get(LOG_LEVEL_ENV)


def configure_logging(level: str | None = None) -> str:
    """Send ``deepfix_rag`` and ``apscheduler`` records to stdout, and return the level.

    Called once when the app is built. It is safe to call again: ``dictConfig``
    replaces the handler these loggers hold, so repeated calls never stack a
    second handler and never print a line twice.
    """
    requested = level if level is not None else requested_level()
    chosen = normalize_level(requested)
    dictConfig(_config(chosen))
    if (requested or "").strip() and (requested or "").strip().upper() not in LOG_LEVELS:
        logging.getLogger("deepfix_rag.api").warning(
            "%s is not a Python logging level. Using %s; set it to one of %s.",
            LOG_LEVEL_ENV,
            DEFAULT_LOG_LEVEL,
            ", ".join(LOG_LEVELS),
        )
    return chosen


def _config(level: str) -> dict[str, object]:
    return {
        "version": 1,
        # mlflow, httpx, and alembic are configured by their own packages. A
        # shutdown of this process is not a reason to disable them.
        "disable_existing_loggers": False,
        "formatters": {
            CONSOLE_HANDLER: {
                "format": LOG_FORMAT,
                "datefmt": LOG_DATE_FORMAT,
            }
        },
        "handlers": {
            CONSOLE_HANDLER: {
                "class": "logging.StreamHandler",
                "formatter": CONSOLE_HANDLER,
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            name: {
                "handlers": [CONSOLE_HANDLER],
                "level": level,
                "propagate": True,
            }
            for name in APP_LOGGERS
        },
    }
