"""Small process resource samplers for long-running offline jobs."""

from __future__ import annotations

import os
import threading
from pathlib import Path


class PeakRssSampler:
    """Sample the current Linux process RSS without inherited rusage history."""

    def __init__(self, interval_seconds: float = 0.05):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.interval_seconds = interval_seconds
        self.page_size = os.sysconf("SC_PAGE_SIZE")
        self.statm_path = Path(f"/proc/{os.getpid()}/statm")
        self.peak_bytes = 0
        self.baseline_bytes: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> int | None:
        """Read current resident bytes, or return None when procfs is absent."""
        try:
            fields = self.statm_path.read_text(encoding="ascii").split()
            resident_bytes = int(fields[1]) * self.page_size
        except (OSError, IndexError, ValueError):
            return None
        self.peak_bytes = max(self.peak_bytes, resident_bytes)
        return resident_bytes

    def start(self) -> "PeakRssSampler":
        if self._thread is not None:
            raise RuntimeError("Peak RSS sampler is already running")
        self._stop.clear()
        self.baseline_bytes = self.sample()
        self._thread = threading.Thread(target=self._run, name="peak-rss-sampler", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample()

    def stop(self) -> float | None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None
        self.sample()
        if not self.peak_bytes:
            return None
        return self.peak_bytes / (1024 * 1024)

    @property
    def peak_delta_mib(self) -> float | None:
        """Peak RSS above the process baseline captured by :meth:`start`."""
        if self.baseline_bytes is None or not self.peak_bytes:
            return None
        return max(0, self.peak_bytes - self.baseline_bytes) / (1024 * 1024)
