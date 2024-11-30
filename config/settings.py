# config/settings.py

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base directory for the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Configuration settings
CONFIG = {
    # Worker settings
    'max_workers': int(os.getenv('MAX_WORKERS', '3')),
    'max_queue_size': int(os.getenv('MAX_QUEUE_SIZE', '20')),

    # Transcription settings
    'transcription': {
        'api_url': os.getenv('TRANSCRIPTION_API_URL', 'https://api.groq.com/openai/v1/audio/transcriptions'),
        'model': os.getenv('TRANSCRIPTION_MODEL', 'whisper-large-v3'),
        'response_format': os.getenv('TRANSCRIPTION_FORMAT', 'json'),
        'language': os.getenv('TRANSCRIPTION_LANGUAGE', None),
        'temperature': os.getenv('TRANSCRIPTION_TEMPERATURE', '0'),  # Added temperature setting
        'timestamp_granularities': os.getenv('TIMESTAMP_GRANULARITIES', 'segment').split(','),
    },

    'max_file_size': 25 * 1024 * 1024,  # 25 MB
    'supported_formats': ['flac', 'mp3', 'mp4', 'mpeg', 'mpga', 'm4a', 'ogg', 'wav', 'webm'],

    # Chunk settings
    'chunk_max_size_bytes': 25 * 1024 * 1024,  # 25 MB
    'chunk_duration_sec': int(os.getenv('CHUNK_DURATION_SEC', '300')),  # Chunk duration in seconds

    # Directory settings
    'temp_dir': BASE_DIR / 'temp',
    'downloaded_videos_dir': BASE_DIR / 'downloaded_videos',
    'output_dir': BASE_DIR / 'transcripts',
    'logs_dir': BASE_DIR / 'logs',

    # Logging settings
    'log_file': os.getenv('LOG_FILE', 'transcriber.log'),

    # API settings
    'api_timeout': int(os.getenv('API_TIMEOUT', '300')),
    'api_key': os.getenv('GROQ_API_KEY'),
}
