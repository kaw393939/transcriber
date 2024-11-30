# models/stats.py

from dataclasses import dataclass, field
from typing import Optional
import threading
from core.logger import setup_logger  # Adjusted import statement

logger = setup_logger(__name__)  # Initialized logger using setup_logger

@dataclass
class ProcessingStats:
    """Statistics for processing stages, ensuring thread-safe updates."""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed: float = 0.0
    eta: Optional[float] = None  # Changed to float for consistency
    progress: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs) -> None:
        """
        Update processing statistics in a thread-safe manner.

        Args:
            **kwargs: Key-value pairs of statistics to update.
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            if self.total_bytes > 0:
                self.progress = min(100.0, (self.downloaded_bytes / self.total_bytes) * 100)
            else:
                self.progress = 0.0  # Avoid division by zero
            logger.debug(f"ProcessingStats updated: {self.__dict__}")
