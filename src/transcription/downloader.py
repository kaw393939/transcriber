from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime
import json
import threading
from typing import Dict, Optional, Tuple, Union, List
import yt_dlp
import shutil
import re
import unicodedata
import os
import time

# Dynamically add project root to PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from models.tasks import TranscriptionTask, TaskStatus
from config.settings import CONFIG
from core.logger import setup_logger

logger = setup_logger(__name__)

class VideoDownloader:
    """Handles downloading of videos and extraction of metadata using yt-dlp."""

    def __init__(self, base_output_dir: Optional[Path] = None):
        """Initialize the VideoDownloader with a base output directory."""
        self.base_output_dir = Path(base_output_dir or CONFIG.get('output_dir', 'downloads'))
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = CONFIG.get('max_retries', 3)
        self.retry_delay = CONFIG.get('retry_delay', 5)
        self.lock = threading.Lock()
        self.download_timeout = CONFIG.get('download_timeout', 3600)  # 1 hour default
        self.verify_timeout = CONFIG.get('verify_timeout', 300)  # 5 minutes for verification

    def sanitize_filename(self, filename: str) -> str:
        """Create a clean, filesystem-safe filename."""
        if not filename:
            return "untitled"

        filename = unicodedata.normalize('NFKD', filename)
        filename = filename.encode('ASCII', 'ignore').decode()
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'[-\s]+', '-', filename).strip('-')
        
        if len(filename) > 100:
            filename = filename[:100]
            
        return filename.lower() or "untitled"

    def create_video_directory(self, video_id: str, title: str) -> Path:
        """Create a unique directory for the video using ID and sanitized title."""
        sanitized_title = self.sanitize_filename(title)
        dir_name = f"{video_id}-{sanitized_title[:50]}"
        video_dir = self.base_output_dir / dir_name

        with self.lock:
            video_dir.mkdir(exist_ok=True, parents=True)
            (video_dir / "audio").mkdir(exist_ok=True)
            (video_dir / "chunks").mkdir(exist_ok=True)
            (video_dir / "transcripts").mkdir(exist_ok=True)
            (video_dir / "temp").mkdir(exist_ok=True)  # Add temp directory

        return video_dir
    def prepare_download_options(self, task: TranscriptionTask, video_dir: Path) -> Dict:
            """Prepare yt-dlp options with improved handling for large files."""
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

                except Exception as e:
                    logger.error(f"Error in progress hook for Task {task.id}: {str(e)}")

            temp_dir = video_dir / "temp"
            output_template = str(temp_dir / "%(id)s.%(ext)s")

            return {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'progress_hooks': [progress_hook],
                'quiet': True,
                'noplaylist': True,
                'extract_flat': False,
                'writesubtitles': False,  # Changed to False as we handle transcription separately
                'writeautomaticsub': False,
                'retries': self.max_retries,
                'retry_sleep': self.retry_delay,
                'socket_timeout': CONFIG.get('api_timeout', 300),
                'fragment_retries': 10,
                'extractor_retries': 5,
                'file_access_retries': 5,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                    'preferredquality': '192',
                    'nopostoverwrites': False,
                }],
                # Add FFmpeg arguments directly
                'postprocessor_args': [
                    '-af', 'aformat=sample_fmts=s16:sample_rates=16000:channel_layouts=mono',
                ],
            }

    def save_metadata(self, task: TranscriptionTask, info: Dict, video_dir: Path) -> None:
        """Save video metadata to JSON file."""
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
            
            with task._lock:
                task.metadata['video_metadata'] = metadata
                task.title = metadata['processed_title']
                
        except Exception as e:
            logger.error(f"Task {task.id}: Failed to save metadata: {e}")

    def verify_wav_file(self, wav_path: Path, timeout: int = 30) -> bool:
        """Verify that the WAV file exists and is valid."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if wav_path.exists() and wav_path.stat().st_size > 0:
                try:
                    # Try to read the file header
                    with open(wav_path, 'rb') as f:
                        header = f.read(44)  # WAV header is 44 bytes
                        if len(header) == 44 and header.startswith(b'RIFF') and b'WAVE' in header:
                            return True
                except Exception as e:
                    logger.error(f"Error verifying WAV file {wav_path}: {e}")
            time.sleep(1)
        return False

    def wait_for_file(self, file_path: Path, timeout: int = 300) -> bool:
        """Wait for a file to appear and be fully written."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if file_path.exists():
                try:
                    # Try to open the file to ensure it's fully written
                    with open(file_path, 'rb') as f:
                        f.seek(-1024, os.SEEK_END)  # Try to read the last 1KB
                        f.read(1024)
                    return True
                except (IOError, OSError):
                    time.sleep(1)
            time.sleep(1)
        return False

    def download_video(self, task: TranscriptionTask) -> Tuple[bool, Optional[str]]:
        """Download video and extract audio with improved error handling."""
        logger.info(f"Task {task.id}: Starting download for URL: {task.url}")
        temp_dir = None
        
        try:
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
            
            # Save metadata
            self.save_metadata(task, info, video_dir)

            # Set up paths
            temp_dir = video_dir / "temp"
            final_audio_dir = video_dir / "audio"
            final_wav = final_audio_dir / f"{video_id}.wav"

            # Download with retry logic
            ydl_opts = self.prepare_download_options(task, video_dir)
            download_success = False
            error_msg = None
            
            for attempt in range(self.max_retries):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([task.url])
                    download_success = True
                    break
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"Download attempt {attempt + 1} failed: {error_msg}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                        # Clear temp directory for retry
                        if temp_dir and temp_dir.exists():
                            try:
                                shutil.rmtree(str(temp_dir))
                                temp_dir.mkdir(exist_ok=True)
                            except Exception as cleanup_error:
                                logger.warning(f"Failed to clean temp directory before retry: {cleanup_error}")

            if not download_success:
                return False, f"Failed to download after {self.max_retries} attempts: {error_msg}"

            # Find and verify the WAV file
            wav_files = list(temp_dir.glob("*.wav"))
            if not wav_files:
                return False, "WAV file not found after download and conversion"

            # Move to final location
            temp_wav = wav_files[0]
            try:
                if final_wav.exists():
                    final_wav.unlink()
                shutil.move(str(temp_wav), str(final_wav))
            except Exception as e:
                return False, f"Failed to move WAV file to final location: {str(e)}"

            # Verify the final file
            if not self.verify_wav_file(final_wav):
                return False, "Failed to verify final WAV file"

            # Wait for file to be fully written
            if not self.wait_for_file(final_wav):
                return False, "Timeout waiting for file to be fully written"

            # Set the path and clean up
            task.temp_video_path = final_wav
            logger.info(f"Task {task.id}: Audio file ready at {final_wav}")
            
            try:
                if temp_dir and temp_dir.exists():
                    shutil.rmtree(str(temp_dir))
            except Exception as e:
                logger.warning(f"Failed to clean up temp directory: {e}")

            return True, None

        except Exception as e:
            error_msg = f"Unexpected error during video download: {str(e)}"
            logger.exception(f"Task {task.id}: {error_msg}")
            # Cleanup on error
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(str(temp_dir))
                except Exception:
                    pass
            return False, error_msg

    def cleanup_task(self, task: TranscriptionTask) -> None:
        """Clean up any remaining temporary files for a task."""
        try:
            video_dir = Path(task.metadata.get('video_dir', ''))
            if video_dir.exists():
                temp_dir = video_dir / "temp"
                if temp_dir.exists():
                    shutil.rmtree(str(temp_dir))
        except Exception as e:
            logger.error(f"Error cleaning up task {task.id}: {e}")