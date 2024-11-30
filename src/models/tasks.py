# models/tasks.py

from enum import Enum
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict


class TaskStatus(Enum):
    PENDING = 'Pending'
    DOWNLOADING = 'Downloading'
    SPLITTING = 'Splitting'
    TRANSCRIBING = 'Transcribing'
    MERGING = 'Merging'
    COMPLETED = 'Completed'
    FAILED = 'Failed'
    CANCELLED = 'Cancelled'
    PAUSED = 'Paused'


class TaskStats:
    def __init__(self):
        self.progress: float = 0.0
        self.total_bytes: int = 0
        self.downloaded_bytes: int = 0
        self.speed: float = 0.0
        self.eta: float = 0.0


class TranscriptionMetadata:
    def __init__(self):
        self.word_count: int = 0
        self.detected_language: str = ''
        self.language_probability: float = 0.0
        self.merged_transcript_path: Optional[str] = None


class TranscriptionTask:
    def __init__(self, url: str):
        self.id: str = str(uuid.uuid4())
        self.url: str = url
        self.title: str = ''
        self.status: TaskStatus = TaskStatus.PENDING
        self.error: Optional[str] = None
        self.created_at: datetime = datetime.now()
        self.stats: TaskStats = TaskStats()
        self.metadata: Dict = {}
        self.video_metadata: Dict = {}
        self.transcription_metadata: TranscriptionMetadata = TranscriptionMetadata()
        self.temp_video_path: Optional[Path] = None
        self._lock: threading.Lock = threading.Lock()

    def update_status(self, status: TaskStatus):
        """Update the status of the task in a thread-safe manner."""
        with self._lock:
            self.status = status

    def set_error(self, error_message: str):
        """Set an error message for the task."""
        with self._lock:
            self.error = error_message

    def can_resume(self) -> bool:
        """Determine if the task can be resumed based on its current status."""
        with self._lock:
            return self.status in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.PAUSED}

    # Additional methods can be added as needed
