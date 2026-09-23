from datetime import datetime, timezone
from time import monotonic


class DowntimeTracker:
    def __init__(self) -> None:
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self._started_monotonic: float | None = None
        self._duration_seconds: float | None = None

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._started_monotonic = monotonic()

    def stop(self) -> None:
        if self._started_monotonic is None:
            return

        self.finished_at = datetime.now(timezone.utc)
        self._duration_seconds = monotonic() - self._started_monotonic

    @property
    def duration_seconds(self) -> float | None:
        return self._duration_seconds