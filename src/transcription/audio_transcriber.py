# transcription/audio_transcriber.py

import subprocess
import tempfile
import httpx
import json
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from threading import Lock
from datetime import datetime, timedelta
import backoff
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib

from config.settings import CONFIG
from models.tasks import TranscriptionTask, TaskStatus
from core.logger import setup_logger

logger = setup_logger(__name__)

class RateLimit:
    """Rate limit tracker."""
    def __init__(self, window_seconds: int, max_requests: int):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.requests: List[datetime] = []
        self.lock = Lock()

    def can_request(self) -> Tuple[bool, float]:
        """Check if request is allowed and return wait time if not."""
        now = datetime.now()
        with self.lock:
            # Remove old requests
            self.requests = [t for t in self.requests 
                           if (now - t).total_seconds() < self.window_seconds]
            
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True, 0
            
            # Calculate wait time
            oldest = self.requests[0]
            wait_time = self.window_seconds - (now - oldest).total_seconds()
            return False, max(0, wait_time)

class AudioTranscriber:
    """Enhanced audio transcription using Groq API with advanced features."""

    def __init__(self):
        # Existing configuration
        self.api_key = CONFIG.get('api_key', '')
        self.api_url = CONFIG['transcription'].get('api_url', '')
        self.model = CONFIG['transcription'].get('model', 'whisper-large-v3')
        self.language = CONFIG['transcription'].get('language', None)
        
        # API settings
        self.max_retries = CONFIG.get('max_retries', 3)
        self.retry_delay = CONFIG.get('retry_delay', 30)
        self.api_timeout = CONFIG.get('api_timeout', 300)
        
        # Enhanced settings
        self.max_chunk_size = 25 * 1024 * 1024  # 25MB
        self.max_workers = CONFIG.get('max_workers', 3)
        self.cache_dir = Path(CONFIG.get('cache_dir', 'cache'))
        self.cache_dir.mkdir(exist_ok=True)
        
        # Rate limiting
        self.rate_limiter = RateLimit(
            window_seconds=CONFIG.get('rate_limit_window', 60),
            max_requests=CONFIG.get('rate_limit_requests', 50)
        )
        
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

    def get_cache_path(self, audio_path: Path) -> Path:
        """Generate cache path for processed audio."""
        hash_str = hashlib.sha256(str(audio_path).encode()).hexdigest()[:12]
        return self.cache_dir / f"{hash_str}_{audio_path.stem}.mp3"

    def verify_audio(self, file_path: Path) -> Dict:
        """Verify audio file format and get metadata."""
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {result.stderr}")
        return json.loads(result.stdout)

    def preprocess_audio(self, audio_path: Path, task: TranscriptionTask) -> Optional[Path]:
        """Enhanced audio preprocessing with caching."""
        try:
            cache_path = self.get_cache_path(audio_path)
            if cache_path.exists():
                logger.info(f"Using cached audio: {cache_path}")
                return cache_path

            temp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            temp_path = Path(temp_file.name)
            temp_file.close()

            # Enhanced FFmpeg settings for better quality
            cmd = [
                'ffmpeg', '-y',
                '-i', str(audio_path),
                '-vn',                    
                '-acodec', 'libmp3lame',
                '-ar', '16000',          
                '-ac', '1',              
                '-b:a', '128k',
                '-filter:a', 'volume=1.0,highpass=f=40,lowpass=f=7000',  # Audio filtering
                '-map_metadata', '-1',
                str(temp_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {result.stderr}")

            # Verify
            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise RuntimeError("Failed to create audio file")

            # Get audio info
            audio_info = self.verify_audio(temp_path)
            logger.debug(f"Audio info: {json_info}")

            # Cache the file if it's valid
            if temp_path.stat().st_size <= self.max_chunk_size:
                temp_path.rename(cache_path)
                return cache_path
            else:
                raise ValueError("Audio file too large")

        except Exception as e:
            logger.error(f"Task {task.id}: Audio preprocessing failed: {str(e)}")
            if temp_path.exists():
                temp_path.unlink()
            return None

    @backoff.on_exception(
        backoff.expo,
        (httpx.HTTPError, RuntimeError),
        max_tries=3,
        max_time=300
    )
    async def transcribe_chunk_async(self, chunk_path: Path, task: TranscriptionTask) -> bool:
        """Async version of chunk transcription."""
        temp_file = None
        try:
            # Check rate limit
            can_request, wait_time = self.rate_limiter.can_request()
            if not can_request:
                logger.info(f"Rate limit - waiting {wait_time}s")
                await asyncio.sleep(wait_time)

            # Preprocess
            temp_file = self.preprocess_audio(chunk_path, task)
            if not temp_file:
                return False

            headers = {'Authorization': f"Bearer {self.api_key}"}
            
            async with httpx.AsyncClient() as client:
                with open(temp_file, 'rb') as f:
                    files = [
                        ('file', (temp_file.name, f, 'application/octet-stream')),
                        ('model', (None, self.model)),
                        ('response_format', (None, 'json'))
                    ]
                    
                    if self.language:
                        files.append(('language', (None, self.language)))

                    response = await client.post(
                        self.api_url,
                        headers=headers,
                        files=files,
                        timeout=self.api_timeout
                    )

                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', self.retry_delay))
                        raise RuntimeError(f"Rate limit exceeded - retry after {retry_after}s")

                    response.raise_for_status()
                    result = response.json()

                    # Save results
                    transcripts_dir = Path(task.metadata['transcripts_dir'])
                    base_name = chunk_path.stem
                    
                    # Save with enhanced metadata
                    transcription_data = {
                        'transcription': result,
                        'metadata': {
                            'chunk_path': str(chunk_path),
                            'processed_at': datetime.now().isoformat(),
                            'model': self.model,
                            'language': result.get('language', self.language),
                            'confidence': result.get('confidence', None)
                        }
                    }

                    json_path = transcripts_dir / f"{base_name}.json"
                    text_path = transcripts_dir / f"{base_name}.txt"

                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(transcription_data, f, indent=2)

                    with open(text_path, 'w', encoding='utf-8') as f:
                        f.write(result.get('text', ''))

                    # Update task metadata
                    with task._lock:
                        task.transcription_metadata.word_count += len(result.get('text', '').split())
                        task.transcription_metadata.detected_language = result.get('language', 
                                                                                self.language)
                        # Add confidence scores if available
                        if 'confidence' in result:
                            if not hasattr(task.transcription_metadata, 'confidence_scores'):
                                task.transcription_metadata.confidence_scores = []
                            task.transcription_metadata.confidence_scores.append(result['confidence'])

                    logger.info(f"Task {task.id}: Transcribed {chunk_path.name}")
                    return True

        except Exception as e:
            logger.error(f"Transcription error: {str(e)}")
            return False

        finally:
            # Cleanup temp files
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass

    def transcribe_chunk(self, chunk_path: Path, task: TranscriptionTask) -> bool:
        """Synchronous wrapper for async transcription."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.transcribe_chunk_async(chunk_path, task)
            )
        finally:
            loop.close()

    async def transcribe_chunks_async(self, task: TranscriptionTask) -> bool:
        """Async batch processing of chunks."""
        try:
            chunks_info = task.metadata.get("chunks_info", {}).get("chunks", [])
            if not chunks_info:
                raise ValueError("No chunks found")

            chunks_dir = Path(task.metadata["chunks_info"]["chunks_directory"])
            if not chunks_dir.exists():
                raise FileNotFoundError("Chunks directory not found")

            # Setup directories
            transcripts_dir = chunks_dir.parent / "transcripts"
            transcripts_dir.mkdir(exist_ok=True)
            task.metadata['transcripts_dir'] = str(transcripts_dir)

            # Process chunks with concurrency limit
            failed_chunks = []
            tasks = []
            
            for chunk_info in chunks_info:
                chunk_path = chunks_dir / chunk_info["relative_path"]
                tasks.append(self.transcribe_chunk_async(chunk_path, task))

            # Process in batches
            results = await asyncio.gather(*tasks)
            
            failed_chunks = [
                chunk_info["relative_path"]
                for chunk_info, success in zip(chunks_info, results)
                if not success
            ]

            if failed_chunks:
                task.metadata['failed_chunks'] = failed_chunks
                return False

            return True

        except Exception as e:
            logger.error(f"Task {task.id}: Transcription failed: {str(e)}")
            task.set_error(str(e))
            return False

    def transcribe_all_chunks(self, task: TranscriptionTask) -> bool:
        """Enhanced synchronous wrapper for batch processing."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.transcribe_chunks_async(task)
            )
        finally:
            loop.close()