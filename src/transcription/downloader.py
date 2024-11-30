from __future__ import annotations
import sys
from pathlib import Path

# Dynamically add project root to PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from datetime import datetime
import json
import threading
from typing import Dict, Optional, Tuple
import yt_dlp
import shutil
import re
import unicodedata

from models.tasks import TranscriptionTask, TaskStatus
from config.settings import CONFIG
from core.logger import setup_logger

logger = setup_logger(__name__)

class VideoDownloader:
    """
    Handles downloading of videos and extraction of metadata using yt-dlp.
    """

    def __init__(self, base_output_dir: Optional[Path] = None):
        """
        Initialize the VideoDownloader with a base output directory.

        Args:
            base_output_dir (Optional[Path]): Base directory for all downloads.
                                              Defaults to CONFIG['output_dir'] if not specified.
        """
        self.base_output_dir = Path(base_output_dir or CONFIG.get('output_dir', 'downloads'))
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = CONFIG.get('max_retries', 3)
        self.retry_delay = CONFIG.get('retry_delay', 5)
        self.lock = threading.Lock()

    def sanitize_filename(self, filename: str) -> str:
        """
        Create a clean, filesystem-safe filename.

        Args:
            filename (str): Original filename

        Returns:
            str: Sanitized filename
        """
        if not filename:
            return "untitled"

        # Normalize unicode characters
        filename = unicodedata.normalize('NFKD', filename)
        
        # Convert to ASCII, dropping non-ASCII characters
        filename = filename.encode('ASCII', 'ignore').decode()
        
        # Remove or replace problematic characters
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'[-\s]+', '-', filename).strip('-')
        
        # Ensure the filename isn't too long
        max_length = 100  # Reasonable maximum length
        if len(filename) > max_length:
            filename = filename[:max_length]
            
        # Ensure we have something valid
        if not filename:
            filename = "untitled"
            
        return filename.lower()

    def create_video_directory(self, video_id: str, title: str) -> Path:
        """
        Create a unique directory for the video using ID and sanitized title.

        Args:
            video_id (str): YouTube video ID
            title (str): Video title

        Returns:
            Path: Path to the created directory
        """
        sanitized_title = self.sanitize_filename(title)
        dir_name = f"{video_id}-{sanitized_title[:50]}"
        video_dir = self.base_output_dir / dir_name

        with self.lock:
            video_dir.mkdir(exist_ok=True, parents=True)
            (video_dir / "audio").mkdir(exist_ok=True)
            (video_dir / "chunks").mkdir(exist_ok=True)
            (video_dir / "transcripts").mkdir(exist_ok=True)

        return video_dir

    def prepare_download_options(self, task: TranscriptionTask, video_dir: Path) -> Dict:
        """
        Prepare yt-dlp options for video download.
        """
        def progress_hook(d):
            try:
                if d['status'] == 'downloading':
                    total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    speed = d.get('speed', 0)
                    eta = d.get('eta', 0)

                    with task._lock:
                        task.stats.total_bytes = total
                        task.stats.downloaded_bytes = downloaded
                        task.stats.speed = speed
                        task.stats.eta = eta

                        if total > 0:
                            task.stats.progress = (downloaded / total) * 100
                            task.metadata.update({
                                'download_speed': f"{speed / 1024 / 1024:.2f} MB/s" if speed else "N/A",
                                'time_remaining': f"{eta:.0f} seconds" if eta else "N/A",
                                'downloaded_size': f"{downloaded / 1024 / 1024:.1f}MB",
                                'total_size': f"{total / 1024 / 1024:.1f}MB"
                            })

                elif d['status'] == 'finished':
                    with task._lock:
                        task.stats.progress = 100.0
                        task.metadata['download_completed_at'] = datetime.now().isoformat()
                        filename = d.get('filename', '')
                        if filename:
                            logger.debug(f"Finished downloading file: {filename}")
                            task.metadata['downloaded_filename'] = filename
                            # Set the temp_video_path based on the final converted WAV file
                            if filename.endswith('.webm'):
                                wav_path = Path(filename).with_suffix('.wav')
                                task.temp_video_path = wav_path
                                logger.debug(f"Set temp_video_path to: {wav_path}")

            except Exception as e:
                logger.error(f"Error in progress hook for Task {task.id}: {str(e)}")

        # Sanitize the output template to avoid special characters
        output_template = "%(id)s.%(ext)s"  # Simplified template using just the video ID

        return {
            'format': 'bestaudio/best',
            'outtmpl': str(video_dir / output_template),
            'progress_hooks': [progress_hook],
            'quiet': True,
            'noplaylist': True,
            'extract_flat': False,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'retries': self.max_retries,
            'retry_sleep': self.retry_delay,
            'socket_timeout': CONFIG.get('api_timeout', 300),
            'max_filesize': CONFIG.get('max_file_size', None),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192',
            }],
        }

    def save_metadata(self, task: TranscriptionTask, info: Dict, video_dir: Path) -> None:
        """
        Save video metadata to JSON file.

        Args:
            task (TranscriptionTask): The task containing video URL and metadata
            info (Dict): Video information from yt-dlp
            video_dir (Path): Video directory path
        """
        metadata = {
            'title': info.get('title'),
            'description': info.get('description'),
            'duration': info.get('duration'),
            'upload_date': info.get('upload_date'),
            'uploader': info.get('uploader'),
            'channel_id': info.get('channel_id'),
            'view_count': info.get('view_count'),
            'like_count': info.get('like_count'),
            'comment_count': info.get('comment_count'),
            'tags': info.get('tags', []),
            'categories': info.get('categories', []),
            'language': info.get('language'),
            'automatic_captions': bool(info.get('automatic_captions')),
            'subtitles': bool(info.get('subtitles')),
            'download_timestamp': datetime.now().isoformat(),
            'video_url': info.get('webpage_url'),
            'format_id': info.get('format_id'),
            'ext': info.get('ext'),
            'audio_channels': info.get('audio_channels'),
            'filesize_approx': info.get('filesize_approx'),
            'duration_string': info.get('duration_string'),
            'processed_title': self.sanitize_filename(info.get('title', '')),
        }

        metadata_path = video_dir / 'metadata.json'
        try:
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            logger.info(f"Task {task.id}: Metadata saved to {metadata_path}")
            
            # Store key metadata in task
            with task._lock:
                task.metadata['video_metadata'] = metadata
                task.title = metadata['processed_title']
                
        except Exception as e:
            logger.error(f"Task {task.id}: Failed to save metadata to {metadata_path}: {e}")

    def download_video(self, task: TranscriptionTask) -> Tuple[bool, Optional[str]]:
            """
            Download video and extract audio.
            """
            logger.info(f"Task {task.id}: Starting download_video for URL: {task.url}")
            try:
                # Validate URL
                if not task.url or not task.url.strip():
                    return False, "Invalid or empty URL"

                # Extract video info
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                    try:
                        info = ydl.extract_info(task.url, download=False)
                    except Exception as e:
                        return False, f"Failed to fetch video info: {str(e)}"

                video_id = info.get('id')
                if not video_id:
                    return False, "Could not retrieve video ID"

                # Create directory structure
                video_dir = self.create_video_directory(video_id, info.get('title', 'untitled'))
                task.metadata['video_dir'] = str(video_dir)
                
                # Save metadata early
                self.save_metadata(task, info, video_dir)

                # Download the video/audio
                ydl_opts = self.prepare_download_options(task, video_dir)
                
                # Download and convert to WAV
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([task.url])

                # The expected WAV file path
                expected_wav = video_dir / f"{video_id}.wav"
                
                # Verify the WAV file exists
                if not expected_wav.exists():
                    # Try to find any WAV file in the directory as fallback
                    wav_files = list(video_dir.glob("*.wav"))
                    if wav_files:
                        expected_wav = wav_files[0]
                        logger.info(f"Found alternative WAV file: {expected_wav}")
                    else:
                        return False, "WAV file not found after download and conversion"

                # Set the final audio path
                task.temp_video_path = expected_wav
                logger.info(f"Task {task.id}: Audio file ready at {expected_wav}")

                return True, None

            except Exception as e:
                error_msg = f"Unexpected error during video download: {str(e)}"
                logger.exception(f"Task {task.id}: {error_msg}")
                return False, error_msg