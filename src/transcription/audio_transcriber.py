# transcription/audio_transcriber.py

import subprocess
import tempfile
import httpx
from pathlib import Path
import json
from datetime import datetime
from threading import Lock
from typing import Optional, Union, List

from config.settings import CONFIG
from models.tasks import TranscriptionTask, TaskStatus
from core.logger import setup_logger

logger = setup_logger(__name__)


class AudioTranscriber:
    """Manages audio transcription using Groq API."""

    def __init__(self):
        """Initialize AudioTranscriber with configuration and thread lock."""
        self.api_key = CONFIG.get('api_key', '')
        self.api_url = CONFIG['transcription'].get('api_url', '')
        self.model = CONFIG['transcription'].get('model', 'whisper-large-v3')
        self.response_format = CONFIG['transcription'].get('response_format', 'json')
        self.language = CONFIG['transcription'].get('language', None)
        self.temperature = str(CONFIG['transcription'].get('temperature', '0'))  # Ensure it's a string
        self.timestamp_granularities = CONFIG['transcription'].get('timestamp_granularities', ['segment'])
        self.lock = Lock()
        self.max_chunk_size_bytes = 25 * 1024 * 1024  # 25 MB

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitize a filename to be URL-safe and lowercase, with no spaces.

        Args:
            filename (str): The filename to sanitize

        Returns:
            str: Sanitized filename
        """
        import re
        filename = re.sub(r'[^a-zA-Z0-9-_\.]', '-', filename)
        filename = re.sub(r'-+', '-', filename)
        return filename.strip('-').lower()

    def verify_audio_format(self, file_path: Path) -> dict:
        """
        Verify audio file format using ffprobe.

        Args:
            file_path (Path): Path to audio file

        Returns:
            dict: Audio format information
        """
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            str(file_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFprobe failed: {result.stderr}")
            return json.loads(result.stdout)
        except Exception as e:
            raise RuntimeError(f"Failed to verify audio format: {str(e)}")

    def preprocess_audio(self, audio_path: Path, task: TranscriptionTask) -> Optional[Path]:
        """
        Preprocess audio file using ffmpeg according to API specifications.

        Args:
            audio_path (Path): Path to the input audio file
            task (TranscriptionTask): Task object for logging context

        Returns:
            Optional[Path]: Path to the preprocessed audio file, None if preprocessing fails
        """
        temp_path = None
        try:
            if not audio_path.exists() or not audio_path.is_file():
                raise FileNotFoundError(f"Audio file not found or is not a file: {audio_path}")

            logger.info(f"Task {task.id}: Preprocessing audio file with ffmpeg")

            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                temp_path = Path(tmp_file.name)

            cmd = [
                'ffmpeg',
                '-y',
                '-i', str(audio_path),
                '-vn',
                '-acodec', 'libmp3lame',
                '-ar', '16000',
                '-ac', '1',
                '-b:a', '128k',
                '-filter:a', 'volume=1.0',
                '-map_metadata', '-1',
                '-f', 'mp3',
                str(temp_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.error(f"Task {task.id}: FFmpeg stderr output: {result.stderr}")
                raise RuntimeError(f"FFmpeg preprocessing failed: {result.stderr}")

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise RuntimeError("FFmpeg created an empty or missing file")

            format_info = self.verify_audio_format(temp_path)
            logger.debug(f"Task {task.id}: Audio format verification: {format_info}")

            file_size = temp_path.stat().st_size
            logger.info(f"Task {task.id}: Successfully preprocessed audio to: {temp_path}")
            logger.info(f"Task {task.id}: Preprocessed file size: {file_size / (1024*1024):.2f} MB")

            # Return the path without deleting it
            return temp_path

        except Exception as e:
            logger.error(f"Task {task.id}: Error preprocessing audio: {str(e)}")
            task.set_error(str(e))
            # Ensure temp_path is deleted if it was created and an error occurred
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                    logger.debug(f"Task {task.id}: Cleaned up temporary file: {temp_path}")
                except Exception as ex:
                    logger.warning(f"Task {task.id}: Failed to delete temporary file: {str(ex)}")
            return None

    def transcribe_chunk(self, audio_chunk_path: Path, task: TranscriptionTask) -> bool:
        """
        Transcribe a single audio chunk.

        Args:
            audio_chunk_path (Path): Path to the audio chunk file
            task (TranscriptionTask): Task object for context

        Returns:
            bool: True if transcription was successful, False otherwise
        """
        try:
            audio_path = audio_chunk_path
            if not audio_path.exists():
                raise FileNotFoundError(f"Audio chunk file not found: {audio_path}")

            if not audio_path.is_file():
                raise ValueError(f"Audio chunk path is not a file: {audio_path}")

            logger.info(f"Task {task.id}: Starting transcription for chunk {audio_path.name}")

            transcripts_dir_str = task.metadata.get("transcripts_dir")
            if not transcripts_dir_str:
                raise ValueError("Transcripts directory not specified in task metadata")

            transcripts_dir = Path(transcripts_dir_str).resolve()
            transcripts_dir.mkdir(parents=True, exist_ok=True)

            # Preprocess audio file (optional)
            temp_file = self.preprocess_audio(audio_path, task)
            if not temp_file:
                raise RuntimeError("Audio preprocessing failed")

            if not temp_file.exists() or not temp_file.is_file():
                raise RuntimeError("Preprocessed audio file does not exist or is not a file")

            file_size = temp_file.stat().st_size
            if file_size > self.max_chunk_size_bytes:
                raise ValueError(f"File size ({file_size / (1024*1024):.2f} MB) exceeds the limit")

            headers = {
                'Authorization': f"Bearer {self.api_key}",
            }

            with open(temp_file, 'rb') as f_audio:
                # Prepare the multipart/form-data
                files = [
                    ('file', (temp_file.name, f_audio, 'application/octet-stream')),
                    ('model', (None, self.model)),
                ]

                # Include optional parameters
                files.append(('response_format', (None, self.response_format)))
                files.append(('temperature', (None, self.temperature)))

                if self.language:
                    files.append(('language', (None, self.language)))

                if self.response_format == 'verbose_json' and self.timestamp_granularities:
                    for granularity in self.timestamp_granularities:
                        files.append(('timestamp_granularities[]', (None, granularity)))

                logger.info(f"Task {task.id}: Sending transcription request to API for chunk {audio_path.name}")

                response = httpx.post(
                    self.api_url,
                    headers=headers,
                    files=files,
                    timeout=CONFIG.get('api_timeout', 300)
                )

            if response.status_code != 200:
                raise RuntimeError(f"API request failed with status {response.status_code}: {response.text}")

            transcription_result = response.json()
            logger.debug(f"Task {task.id}: Received transcription result for chunk {audio_path.name}")

            chunk_filename = audio_path.stem
            json_output_path = transcripts_dir / f"{chunk_filename}.json"
            text_output_path = transcripts_dir / f"{chunk_filename}.txt"

            # Save the transcription results
            with open(json_output_path, 'w', encoding='utf-8') as f_json:
                json.dump({'transcription': transcription_result}, f_json, indent=2, ensure_ascii=False)
                logger.info(f"Task {task.id}: Transcription JSON saved to {json_output_path}")

            with open(text_output_path, 'w', encoding='utf-8') as f_text:
                f_text.write(transcription_result.get('text', ''))
                logger.info(f"Task {task.id}: Transcription text saved to {text_output_path}")

            # Update transcription metadata
            with task._lock:
                tm = task.transcription_metadata
                tm.word_count += len(transcription_result.get('text', '').split())
                tm.detected_language = transcription_result.get('language', self.language)
                tm.language_probability = transcription_result.get('language_probability', 1.0)

            logger.info(f"Task {task.id}: Transcription completed successfully for chunk {audio_path.name}")
            return True

        except Exception as e:
            logger.error(f"Task {task.id}: Error during transcription of chunk {audio_path.name}: {str(e)}")
            task.set_error(str(e))
            return False



    def transcribe_all_chunks(self, task: TranscriptionTask) -> bool:
        """
        Transcribe all audio chunks for the task.

        Args:
            task (TranscriptionTask): Task containing chunks information

        Returns:
            bool: True if all chunks were transcribed successfully, False otherwise
        """
        try:
            chunks_info = task.metadata.get("chunks_info", {}).get("chunks", [])
            if not chunks_info:
                raise ValueError("No chunks information found for transcription")

            chunks_dir_str = task.metadata.get("chunks_info", {}).get("chunks_directory")
            if not chunks_dir_str:
                raise ValueError("Chunks directory not specified in chunks_info")

            chunks_dir = Path(chunks_dir_str)
            if not chunks_dir.exists():
                raise FileNotFoundError(f"Chunks directory not found: {chunks_dir}")

            transcripts_dir = chunks_dir.parent / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            task.metadata['transcripts_dir'] = str(transcripts_dir)

            all_success = True
            for chunk_info in chunks_info:
                chunk_filename = chunk_info.get("relative_path")
                chunk_path = chunks_dir / chunk_filename
                success = self.transcribe_chunk(chunk_path, task)
                if not success:
                    logger.error(f"Task {task.id}: Failed to transcribe chunk {chunk_filename}")
                    all_success = False

            return all_success

        except Exception as e:
            logger.error(f"Task {task.id}: Error transcribing all chunks: {str(e)}")
            task.set_error(str(e))
            return False

    def merge_transcripts(self, task: TranscriptionTask) -> bool:
        """
        Merge all chunk transcripts into a single JSON and plain text file.

        Args:
            task (TranscriptionTask): Task containing transcript information

        Returns:
            bool: True if merging was successful, False otherwise
        """
        try:
            # Validate and get transcripts directory
            transcripts_dir_str = task.metadata.get("transcripts_dir")
            if not transcripts_dir_str:
                raise ValueError("Transcripts directory not specified in task metadata")
            transcripts_dir = Path(transcripts_dir_str).resolve()
            if not transcripts_dir.exists():
                raise ValueError(f"Transcripts directory not found: {transcripts_dir}")

            merged_dir = transcripts_dir / "merged"
            merged_dir.mkdir(parents=True, exist_ok=True)

            # Validate chunks info
            chunks_info = task.metadata.get("chunks_info", {}).get("chunks", [])
            if not chunks_info:
                raise ValueError("No chunks information found for merging transcripts")

            # Process chunks
            logger.info(f"Task {task.id}: Processing {len(chunks_info)} chunks for merging")
            chunks_info_sorted = sorted(chunks_info, key=lambda x: x.get("chunk_index", 0))
            merged_text = []
            merged_segments = []
            total_duration = 0.0
            total_words = 0

            # Process each chunk
            for chunk_info in chunks_info_sorted:
                chunk_filename = Path(chunk_info.get("filename", '')).stem
                json_path = transcripts_dir / f"{chunk_filename}.json"

                if not json_path.exists():
                    logger.warning(f"Task {task.id}: Missing JSON transcript at {json_path}")
                    continue

                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        chunk_data = json.load(f)

                    transcription = chunk_data.get('transcription', {})
                    text = transcription.get('text', '').strip()
                    if text:
                        merged_text.append(text)
                        total_words += len(text.split())

                    # Process segments if available
                    segments = transcription.get('segments', [])
                    for segment in segments:
                        adjusted_segment = segment.copy()
                        adjusted_segment['start'] += total_duration
                        adjusted_segment['end'] += total_duration
                        merged_segments.append(adjusted_segment)

                    # Update duration
                    duration_ms = float(chunk_info.get("duration_ms", 0))
                    total_duration += duration_ms / 1000.0

                    logger.debug(f"Task {task.id}: Processed chunk {chunk_filename}")

                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Task {task.id}: Error processing chunk {json_path}: {str(e)}")
                    continue

            # Verify we have content to merge
            if not merged_text:
                raise ValueError("No transcription text found to merge")

            # Prepare merged content
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = self.sanitize_filename(task.title or task.url)
            if not base_name:
                base_name = "untitled"
            merged_filename_base = f"complete_{base_name}_{timestamp}"

            # Create merged JSON structure
            merged_json = {
                "text": "\n\n".join(merged_text),
                "segments": merged_segments,
                "metadata": {
                    "task_id": task.id,
                    "video_title": task.title,
                    "video_url": task.url,
                    "duration_seconds": total_duration,
                    "total_words": total_words,
                    "chunks_merged": len(merged_segments),
                    "merged_at": datetime.now().isoformat()
                }
            }

            # Save merged content
            merged_json_path = merged_dir / f"{merged_filename_base}.json"
            merged_text_path = merged_dir / f"{merged_filename_base}.txt"

            with open(merged_json_path, 'w', encoding='utf-8') as f:
                json.dump(merged_json, f, indent=2, ensure_ascii=False)
                logger.info(f"Task {task.id}: Saved merged JSON to {merged_json_path}")

            with open(merged_text_path, 'w', encoding='utf-8') as f:
                f.write(merged_json["text"])
                logger.info(f"Task {task.id}: Saved merged text to {merged_text_path}")

            # Update task metadata
            with task._lock:
                # Ensure task.transcription_metadata exists before accessing it
                if not hasattr(task, 'transcription_metadata'):
                    task.transcription_metadata = type('TranscriptionMetadata', (object,), {})()
                task.transcription_metadata.merged_transcript_path = str(merged_text_path)
                task.metadata.update({
                    'merged_transcript_json': str(merged_json_path),
                    'merged_transcript_text': str(merged_text_path),
                    'merge_timestamp': timestamp,
                    'total_words': total_words,
                    'total_duration': total_duration
                })

            logger.info(f"Task {task.id}: Successfully merged transcripts")
            return True

        except Exception as e:
            error_msg = f"Error merging transcripts: {str(e)}"
            logger.error(f"Task {task.id}: {error_msg}")
            task.set_error(error_msg)
            return False
