# Transcription Application - Comprehensive Guide

This repository contains a transcription application designed to download video or audio content, split it into manageable chunks, transcribe the audio using advanced NLP models, and merge the results into a unified transcript. The application is built with Python and uses libraries such as `yt-dlp`, `ffmpeg`, and external APIs for transcription. It also demonstrates advanced development concepts such as threading, asynchronous processing, caching, and rate limiting.

---

## Table of Contents

- [Transcription Application - Comprehensive Guide](#transcription-application---comprehensive-guide)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Project Structure](#project-structure)
  - [Installation and Setup](#installation-and-setup)
    - [Prerequisites](#prerequisites)
    - [Installation Steps](#installation-steps)
  - [How It Works](#how-it-works)
  - [Detailed Component Overview](#detailed-component-overview)
    - [1. Task and Manager](#1-task-and-manager)
    - [2. UI](#2-ui)
    - [3. Downloader](#3-downloader)
    - [4. Splitter](#4-splitter)
    - [5. Transcriber](#5-transcriber)
  - [Configuration](#configuration)
  - [Running the Application](#running-the-application)
  - [Known Issues and Debugging](#known-issues-and-debugging)
  - [Future Improvements](#future-improvements)

---

## Features

- Download video/audio files from URLs.
- Convert media into `.wav` files optimized for transcription.
- Split audio files into chunks of configurable size.
- Transcribe chunks using an external API (e.g., Whisper API).
- Merge transcription results into a single, cohesive output.
- Real-time task monitoring using a command-line UI.
- Thread-safe, rate-limited, and scalable design.

---

## Project Structure

```
project-root/
│
├── src/
│   ├── transcription/
│   │   ├── audio_transcriber.py   # Handles transcription logic
│   │   ├── downloader.py          # Downloads and preprocesses videos
│   │   ├── splitter.py            # Splits audio into chunks
│   │   ├── manager.py             # Task manager for processing jobs
│   │
│   ├── models/
│   │   └── tasks.py               # Definitions of tasks and statuses
│   │
│   ├── core/
│   │   └── logger.py              # Logger setup for detailed debug logs
│   │
│   ├── config/
│   │   ├── settings.py            # Configuration and environment variables
│   │   └── .env                   # Secrets and API keys
│   │
│   ├── ui.py                      # Command-line UI for interacting with tasks
│   └── main.py                    # Entry point for running the application
│
├── requirements.txt               # Dependencies for the project
├── README.md                      # Comprehensive guide (you are here)
└── logs/                          # Directory for log files
```

---

## Installation and Setup

### Prerequisites

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) installed and available in PATH.
- Internet access for downloading content and making API requests.

### Installation Steps

1. **Clone the Repository**

   ```bash
   git clone https://github.com/your-repo-name/transcription-app.git
   cd transcription-app
   ```

2. **Set Up a Virtual Environment**

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set Up the Configuration**

   Create a `.env` file in the `config/` directory (or modify the existing one):

   ```env
   GROQ_API_KEY=your-api-key
   TRANSCRIPTION_API_URL=https://api.groq.com/openai/v1/audio/transcriptions
   MAX_WORKERS=4
   ```

5. **Verify Installation**

   Ensure `FFmpeg` is installed and accessible:

   ```bash
   ffmpeg -version
   ```

---

## How It Works

1. **Initialization:**
   - The `TranscriptionManager` initializes worker threads and a task queue.
   - The `SimpleTranscriptionUI` starts the UI for user interaction.

2. **User Input:**
   - The user provides a video/audio URL via the command-line interface.
   - A `TranscriptionTask` is created and queued for processing.

3. **Processing:**
   - The `TranscriptionManager` assigns tasks to worker threads:
     - **Downloader:** Downloads and extracts audio.
     - **Splitter:** Splits the audio into manageable chunks.
     - **Transcriber:** Sends chunks to an external API for transcription.
     - **Merger:** Combines chunk results into a complete transcript.

4. **Output:**
   - The merged transcript is saved locally as a `.txt` file.

---

## Detailed Component Overview

### 1. Task and Manager

- **`TranscriptionTask` (`models/tasks.py`)**
  - Represents a single transcription task with attributes like `status`, `progress`, and `metadata`.
  - Thread-safe design using locks.

- **`TranscriptionManager` (`transcription/manager.py`)**
  - Orchestrates task execution using a thread pool.
  - Provides methods to add, resume, and process tasks.

### 2. UI

- **`SimpleTranscriptionUI` (`ui.py`)**
  - Command-line interface using `prompt_toolkit`.
  - Displays task status in real time, accepts user input for task management, and supports graceful shutdown.

### 3. Downloader

- **`VideoDownloader` (`transcription/downloader.py`)**
  - Downloads video/audio and extracts metadata using `yt-dlp`.
  - Converts media to `.wav` format with FFmpeg.
  - Handles retries, temporary files, and metadata storage.

### 4. Splitter

- **`AudioSplitter` (`transcription/splitter.py`)**
  - Splits audio into smaller chunks using FFmpeg.
  - Ensures compliance with size and duration constraints.
  - Generates a manifest file with chunk metadata.

### 5. Transcriber

- **`AudioTranscriber` (`transcription/audio_transcriber.py`)**
  - Sends audio chunks to an external API for transcription.
  - Implements rate-limiting and retry mechanisms.
  - Saves transcription results in both JSON and plain text formats.
  - Merges all chunk transcriptions into a cohesive output.

---

## Configuration

All configuration settings are centralized in `config/settings.py` and `.env`.

- **Example `.env` File:**

  ```env
  GROQ_API_KEY=your-api-key
  TRANSCRIPTION_API_URL=https://api.groq.com/openai/v1/audio/transcriptions
  MAX_WORKERS=4
  CHUNK_DURATION_SEC=300
  ```

- **Key Settings:**
  - `MAX_WORKERS`: Number of worker threads.
  - `CHUNK_DURATION_SEC`: Duration of each audio chunk in seconds.
  - `API_TIMEOUT`: Timeout for transcription requests.

---

## Running the Application

1. **Start the Application**

   ```bash
   python main.py
   ```

2. **Interacting with the UI**
   - Enter a valid video/audio URL.
   - Monitor task progress in real time.
   - Quit the application with `Ctrl+C` or `Ctrl+Q`.

3. **Output**
   - Transcripts and metadata are saved in the `output/` directory.

---

## Known Issues and Debugging

1. **Common Errors**
   - `FFmpeg not found`: Ensure FFmpeg is installed and available in PATH.
   - `Invalid API Key`: Double-check your API key in the `.env` file.

2. **Logs**
   - Detailed logs are saved in the `logs/` directory.

3. **Debugging Tips**
   - Run the application in verbose mode: Modify `logger` settings to `DEBUG` in `core/logger.py`.

---

## Future Improvements

- **Add Support for More Formats**: Expand support for video and audio codecs.
- **Enhance UI**: Provide a web-based UI for better accessibility.
- **Custom NLP Models**: Integrate support for fine-tuned transcription models.
- **Real-time Updates**: Implement WebSocket-based updates for a dynamic UI experience.