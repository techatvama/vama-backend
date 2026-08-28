"""
Application logging setup.

Configures two independent loggers:

- "vama"             human-readable request/lifecycle log
- "vama.performance" one JSON object per line, one per request, for
                      stats/percentile analysis (see scripts/analyze_api_performance.py)

On a normal host (local dev, EC2, Docker, systemd) both rotate to files under
settings.log_dir (app.log / api_performance.log), and "vama" also streams to
stdout unless LOG_TO_CONSOLE=false.

On Vercel (or any read-only/ephemeral filesystem — detected via the VERCEL env
var Vercel sets automatically), file handlers are skipped entirely: the
filesystem outside /tmp is read-only, and even /tmp doesn't persist across
invocations or get shared between concurrent ones, so a log file there would
be neither reliable nor inspectable. Both loggers write to stdout instead,
which Vercel captures as Runtime Logs (viewable live in the dashboard or via
`vercel logs --follow`) — this is the "real-time" view on that platform.

Railway (detected via RAILWAY_ENVIRONMENT, set automatically on every Railway
deploy) gets the same treatment: its containers have no persistent disk, so a
rotating file log would just be discarded on every restart/redeploy. Railway
captures stdout as the service's Logs tab, so console-only logging is both
sufficient and the only durable option there.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from config import settings

IS_VERCEL = bool(os.getenv("VERCEL"))
IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))
IS_EPHEMERAL_FS = IS_VERCEL or IS_RAILWAY

_LOG_FILE = os.path.join(settings.log_dir, "app.log")
_PERF_LOG_FILE = os.path.join(settings.log_dir, settings.perf_log_file)
_configured = False
_perf_configured = False


def setup_logging() -> logging.Logger:
    """Idempotently configure and return the 'vama' application logger."""
    global _configured
    logger = logging.getLogger("vama")

    if _configured:
        return logger

    logger.setLevel(settings.log_level.upper())
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not IS_EPHEMERAL_FS:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if IS_EPHEMERAL_FS or settings.log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("vama")


def setup_performance_logging() -> logging.Logger:
    """Idempotently configure and return the 'vama.performance' JSON-lines logger.

    Kept separate from 'vama' so structured performance records never mix with
    (or get parsed as) the human-readable app log, and so it can rotate/be
    shipped independently.
    """
    global _perf_configured
    logger = logging.getLogger("vama.performance")

    if _perf_configured:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    # One JSON object per line (JSONL) — no extra prefix, callers pass
    # a pre-serialized JSON string as the log message.
    json_formatter = logging.Formatter("%(message)s")

    if not IS_EPHEMERAL_FS:
        os.makedirs(settings.log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            _PERF_LOG_FILE,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)

    if IS_EPHEMERAL_FS or settings.log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(json_formatter)
        logger.addHandler(console_handler)

    _perf_configured = True
    return logger


def get_performance_logger() -> logging.Logger:
    return logging.getLogger("vama.performance")
