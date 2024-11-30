# transcription/splitter.py

import subprocess
from pathlib import Path
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import threading
import math

from models.tasks import TranscriptionTask
from core.logger import setup_logger
from config.settings import CONFIG  # Ensure CONFIG is imported for configuration parameters

logger = setup_logger(__name__)


class AudioSplitter:
    """
    Splits audio files into smaller chunks using ffmpeg, ensuring compliance with Groq requirements.
    """

    def __init__(self):
        """Initialize AudioSplitter with configurable parameters."""
        self.chunk_max_size_bytes = CONFIG.get('chunk_max_size_bytes', 25 * 1024 * 1024)  # 25 MB
        self.chunk_duration_sec = CONFIG.get('chunk_duration_sec', 300)  # Default chunk duration: 5 minutes
        self.audio_format = CONFIG.get('audio_format', 'wav')
        self.sample_rate = CONFIG.get('sample_rate', 16000)
        self.channels = CONFIG.get('channels', 1)
        self.lock = threading.Lock()

    def get_audio_duration(self, audio_file_path: Path) -> Optional[float]:
        """
        Get the duration of the audio file using ffprobe.

        Args:
            audio_file_path (Path): Path to the audio file.

        Returns:
            Optional[float]: Duration in seconds if successful, None otherwise.
        """
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(audio_file_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration_str = result.stdout.strip()
            logger.debug(f"Duration output from ffprobe: {duration_str}")

            if duration_str == 'N/A' or not duration_str:
                raise ValueError("Duration not available")

            duration = float(duration_str)
            return duration
        except subprocess.CalledProcessError as e:
            logger.error(f"ffprobe failed: {e.stderr}")
        except ValueError as e:
            logger.error(f"Error parsing duration: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in get_audio_duration: {str(e)}")

        return None

    def split_audio(self, task: TranscriptionTask) -> Optional[List[Dict]]:
        """
        Split audio file into chunks using ffmpeg.

        Args:
            task (TranscriptionTask): TranscriptionTask containing audio file information.

        Returns:
            Optional[List[Dict]]: List of dictionaries containing chunk information, or None if failed.
        """
        try:
            with task._lock:
                video_dir = Path(task.metadata.get('video_dir', ''))
                audio_path = task.temp_video_path
                if not audio_path:
                    raise FileNotFoundError("Audio path not set in task.temp_video_path")
                chunks_dir = video_dir / "chunks"
                chunks_dir.mkdir(parents=True, exist_ok=True)
                task.metadata['chunks_dir'] = str(chunks_dir)

            logger.info(f"Using chunks directory: {chunks_dir.absolute()}")

            if not audio_path.exists():
                error_msg = f"Audio file not found: {audio_path}"
                logger.error(error_msg)
                with task._lock:
                    task.metadata["error"] = error_msg
                return None

            # Get audio duration
            total_duration = self.get_audio_duration(audio_path)
            if total_duration is None:
                error_msg = "Failed to get audio duration."
                logger.error(error_msg)
                with task._lock:
                    task.metadata["error"] = error_msg
                return None

            # Calculate the number of chunks based on desired chunk duration
            num_chunks = max(1, math.ceil(total_duration / self.chunk_duration_sec))
            logger.info(f"Splitting audio into {num_chunks} chunks with duration {self.chunk_duration_sec} seconds each.")

            chunks_info = []
            for i in range(num_chunks):
                start_time = i * self.chunk_duration_sec
                end_time = min((i + 1) * self.chunk_duration_sec, total_duration)
                duration = end_time - start_time

                # Ensure the chunk size does not exceed the maximum allowed size
                estimated_chunk_size = (duration / total_duration) * audio_path.stat().st_size
                if estimated_chunk_size > self.chunk_max_size_bytes:
                    logger.warning(f"Estimated chunk size {estimated_chunk_size} bytes exceeds max size. Adjusting duration.")
                    duration = duration * (self.chunk_max_size_bytes / estimated_chunk_size)
                    end_time = start_time + duration

                # Format timestamps
                start_timestamp = self.format_timestamp_for_filename(start_time)
                end_timestamp = self.format_timestamp_for_filename(end_time)

                chunk_filename = f"chunk_{i:03d}_{start_timestamp}_{end_timestamp}.{self.audio_format}"
                chunk_path = chunks_dir / chunk_filename

                # Build ffmpeg command
                ffmpeg_cmd = [
                    'ffmpeg', '-y', '-i', str(audio_path),
                    '-ss', str(start_time),
                    '-t', str(duration),
                    '-ar', str(self.sample_rate),
                    '-ac', str(self.channels),
                    '-map', '0:a',
                    str(chunk_path)
                ]

                # Run ffmpeg command
                result = subprocess.run(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                if result.returncode != 0:
                    logger.error(f"ffmpeg error for chunk {i}: {result.stderr}")
                    continue

                if not chunk_path.exists():
                    logger.error(f"Failed to create chunk file: {chunk_path}")
                    continue

                chunk_info = self.create_chunk_metadata(
                    chunk_path,
                    start_time * 1000,  # Convert to milliseconds
                    end_time * 1000,
                    i
                )
                chunks_info.append(chunk_info)

                logger.info(f"Created chunk {i + 1}/{num_chunks}: {chunk_path.absolute()} (duration: {duration:.2f}s)")

            if not chunks_info:
                error_msg = "No chunks were created during audio splitting."
                logger.error(error_msg)
                with task._lock:
                    task.metadata["error"] = error_msg
                return None

            manifest = {
                "total_chunks": len(chunks_info),
                "total_duration_ms": total_duration * 1000,
                "chunks": chunks_info,
                "chunks_directory": str(chunks_dir.absolute()),
                "created_at": datetime.now().isoformat()
            }

            manifest_path = chunks_dir / "chunks_manifest.json"
            try:
                with open(manifest_path, "w", encoding='utf-8') as f:
                    json.dump(manifest, f, indent=2)
                logger.info(f"Chunks manifest saved to {manifest_path}")
            except Exception as e:
                logger.exception(f"Failed to save chunks manifest: {e}")
                with task._lock:
                    task.metadata["error"] = f"Failed to save chunks manifest: {e}"
                return None

            with task._lock:
                task.metadata["chunks_info"] = manifest

            logger.info(f"Successfully split audio into {len(chunks_info)} chunks")
            return chunks_info

        except Exception as e:
            logger.exception(f"Error splitting audio for task {task.id}: {e}")
            return None

    def format_timestamp_for_filename(self, seconds: float) -> str:
        """
        Convert seconds to a filename-safe formatted timestamp.

        Args:
            seconds (float): Time in seconds.

        Returns:
            str: Formatted timestamp string (HH_MM_SS_mmm).
        """
        try:
            time = timedelta(seconds=seconds)
            total_seconds = int(time.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            milliseconds = int((time.total_seconds() - total_seconds) * 1000)
            return f"{hours:02d}_{minutes:02d}_{seconds:02d}_{milliseconds:03d}"
        except Exception as e:
            logger.exception(f"Error formatting timestamp for {seconds}s: {e}")
            return "00_00_00_000"

    def create_chunk_metadata(self, chunk_path: Path, start_ms: float, end_ms: float, chunk_index: int) -> Dict:
        """
        Create metadata for an audio chunk.

        Args:
            chunk_path (Path): Path to the chunk file.
            start_ms (float): Start time in milliseconds.
            end_ms (float): End time in milliseconds.
            chunk_index (int): Index of the chunk.

        Returns:
            Dict: Dictionary containing chunk metadata.
        """
        try:
            metadata = {
                "chunk_index": chunk_index,
                "filename": str(chunk_path.absolute()),
                "relative_path": chunk_path.name,
                "start_time": self.format_timestamp_for_metadata(start_ms),
                "end_time": self.format_timestamp_for_metadata(end_ms),
                "duration_ms": end_ms - start_ms,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "audio_format": self.audio_format,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "created_at": datetime.now().isoformat()
            }
            logger.debug(f"Created metadata for chunk {chunk_index}: {metadata}")
            return metadata
        except Exception as e:
            logger.exception(f"Error creating metadata for chunk {chunk_index}: {e}")
            return {}

    def format_timestamp_for_metadata(self, ms: float) -> str:
        """
        Convert milliseconds to a formatted timestamp for metadata display.

        Args:
            ms (float): Time in milliseconds.

        Returns:
            str: Formatted timestamp string (HH:MM:SS.mmm).
        """
        try:
            time = timedelta(milliseconds=ms)
            total_seconds = int(time.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            milliseconds = int(ms % 1000)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
        except Exception as e:
            logger.exception(f"Error formatting timestamp for {ms}ms: {e}")
            return "00:00:00.000"
