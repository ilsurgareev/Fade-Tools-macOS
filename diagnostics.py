"""Local GUI error logs and event-loop stall snapshots."""
import faulthandler
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path


@dataclass
class LoggingResources:
    log_path: Path
    handler: logging.Handler
    excepthook: object
    thread_hook: object
    log_level: int


def setup_logging(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'settings-{os.getpid()}.log'
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s'))
    logger = logging.getLogger()
    resources = LoggingResources(path, handler, sys.excepthook, threading.excepthook, logger.level)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def exception_hook(kind, value, tb):
        logger.error('Unhandled exception', exc_info=(kind, value, tb))
        resources.excepthook(kind, value, tb)

    def thread_exception_hook(args):
        logger.error('Unhandled thread exception: %s', args.thread.name if args.thread else '?',
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        resources.thread_hook(args)

    sys.excepthook = exception_hook
    threading.excepthook = thread_exception_hook
    logger.info('Fade Settings started; process=%s executable=%s', os.getpid(), sys.executable)
    return logger, resources


def restore_logging(logger, resources):
    sys.excepthook = resources.excepthook
    threading.excepthook = resources.thread_hook
    logger.removeHandler(resources.handler)
    logger.setLevel(resources.log_level)
    resources.handler.close()


class GuiWatchdog:
    """Write all Python stacks when Qt's event loop cannot service its timer."""

    def __init__(self, app, path, *, logger=None, interval_ms=1000, timeout_seconds=5):
        from PySide6.QtCore import QTimer
        self.timer = QTimer(app)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._heartbeat)
        self.path = Path(path)
        self.logger = logger or logging.getLogger(__name__)
        self.timeout = timeout_seconds
        self.last_tick = None
        self.stream = None
        self.fatal_handler_installed = False

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a', encoding='utf-8', buffering=1)
        if not faulthandler.is_enabled():
            faulthandler.enable(file=self.stream, all_threads=True)
            self.fatal_handler_installed = True
        self.last_tick = time.monotonic()
        self._arm()
        self.timer.start()
        self.logger.info('GUI watchdog started; timeout=%.1fs stacks=%s', self.timeout, self.path)

    def _arm(self):
        faulthandler.dump_traceback_later(self.timeout, repeat=True, file=self.stream)

    def _heartbeat(self):
        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        if elapsed >= self.timeout:
            self.logger.warning('GUI recovered after an event-loop stall of %.1fs; see %s', elapsed, self.path)
        self._arm()

    def stop(self):
        self.timer.stop()
        faulthandler.cancel_dump_traceback_later()
        if self.fatal_handler_installed:
            faulthandler.disable()
            self.fatal_handler_installed = False
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.logger.info('GUI watchdog stopped')

