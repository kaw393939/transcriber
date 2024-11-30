# models/shared.py

from dataclasses import dataclass, field
from typing import Dict, Optional, List
from enum import Enum
from datetime import datetime
from core.logger import setup_logger  # Adjusted import statement

logger = setup_logger(__name__)  # Initialized logger using setup_logger


class TaskStatus(str, Enum):
    """Enumeration of possible task statuses."""
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    SPLITTING = "SPLITTING"
    TRANSCRIBING = "TRANSCRIBING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


@dataclass
class TaskStatistics:
    """Statistics for task progress and performance."""
    progress: float = 0.0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed: float = 0.0
    eta: float = 0.0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    def to_dict(self) -> Dict:
        """Convert statistics to dictionary."""
        return {
            "progress": self.progress,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None
        }


@dataclass
class VideoMetadata:
    """Metadata about the source video."""
    title: str = ""
    description: str = ""
    duration: Optional[float] = None
    upload_date: Optional[str] = None
    uploader: Optional[str] = None
    channel_id: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    language: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert video metadata to dictionary."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class TranscriptionMetadata:
    """Metadata about the transcription process."""
    model_name: str = "base"
    device: str = "cpu"
    output_formats: List[str] = field(default_factory=lambda: ["txt", "srt"])
    detected_language: Optional[str] = None
    language_probability: float = 0.0
    word_count: int = 0
    merged_transcript_path: Optional[str] = None  # Path to the final merged transcript

    def to_dict(self) -> Dict:
        """Convert transcription metadata to dictionary."""
        return {k: v for k, v in self.__dict__.items() if v is not None}
