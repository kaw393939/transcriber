# src/cli.py

from __future__ import annotations  # Postpones evaluation of type hints (Python 3.7+)
import sys
from pathlib import Path

# Dynamically add project root to PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from typing import Optional, List
import threading
import time
from collections import deque
import humanize
import re

from models.tasks import TranscriptionTask, TaskStatus
from transcription.manager import TranscriptionManager

# Prompt Toolkit imports
from prompt_toolkit import PromptSession
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FormattedTextControl, ScrollablePane, WindowAlign
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.widgets import TextArea, Frame
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

class TranscriptionUI:
    def __init__(self):
        """Initialize the TranscriptionUI with enhanced layout and controls."""
        self.manager = TranscriptionManager()
        self.kb = KeyBindings()
        self.stop_event = threading.Event()
        self.message_history = deque(maxlen=1000)
        self.selected_task_index = 0
        self.history_lock = threading.Lock()
        
        # Create UI components
        self.setup_keybindings()
        self.create_status_windows()
        self.create_log_window()
        self.create_input_area()
        self.create_help_window()
        
        # Create the application
        self.app = Application(
            layout=self.create_layout(),
            key_bindings=self.kb,
            full_screen=True,
            mouse_support=True,
            style=self.get_style(),
            refresh_interval=0.5
        )

    def setup_keybindings(self) -> None:
        """Setup keyboard shortcuts."""
        @self.kb.add('c-c')
        @self.kb.add('c-q')
        def _(event):
            """Handle quit command."""
            self.stop_event.set()
            event.app.exit()

        @self.kb.add('tab')
        def _(event):
            """Cycle through tasks."""
            if self.manager.tasks:
                self.selected_task_index = (self.selected_task_index + 1) % len(self.manager.tasks)
                self.log_message(('class:info', f"Selected task {self.selected_task_index}"))

        @self.kb.add('s-tab')
        def _(event):
            """Cycle through tasks backwards."""
            if self.manager.tasks:
                self.selected_task_index = (self.selected_task_index - 1) % len(self.manager.tasks)
                self.log_message(('class:info', f"Selected task {self.selected_task_index}"))

        @self.kb.add('c-s')
        def _(event):
            """Stop selected task."""
            if task := self.get_selected_task():
                task.update_status(TaskStatus.CANCELLED)
                self.log_message(('class:warning', f"Task cancelled: {task.title}"))

        @self.kb.add('c-r')
        def _(event):
            """Resume selected task if possible."""
            if task := self.get_selected_task():
                if task.can_resume():
                    self.manager.resume_task(task)
                    self.log_message(('class:info', f"Resuming task: {task.title}"))
                else:
                    self.log_message(('class:status.error', "Task cannot be resumed"))

    def create_status_windows(self) -> None:
        """Create the task status display windows."""
        self.task_status_control = FormattedTextControl("")
        self.task_status_window = Frame(
            Window(
                content=self.task_status_control,
                wrap_lines=True,
            ),
            title="Current Tasks",
        )
        
        self.details_control = FormattedTextControl("")
        self.details_window = Frame(
            Window(
                content=self.details_control,
                wrap_lines=True,
            ),
            title="Selected Task Details",
        )

    def create_log_window(self) -> None:
        """Create the log message window."""
        self.log_control = FormattedTextControl("")
        self.log_window = Frame(
            ScrollablePane(
                Window(
                    content=self.log_control,
                    wrap_lines=True,
                )
            ),
            title="Log Messages",
        )

    def create_input_area(self) -> None:
        """Create the input field and controls."""
        self.input_field = TextArea(
            height=1,
            prompt='Enter URL or command: ',
            style='class:input-field',
            multiline=False,
        )
        self.input_field.accept_handler = self.handle_input

    def create_help_window(self) -> None:
        """Create the help information window."""
        self.help_text = HTML("""
            <b>Keyboard Shortcuts:</b>
            • Ctrl+C/Q: Quit
            • Tab/Shift+Tab: Cycle tasks
            • Ctrl+S: Stop selected task
            • Ctrl+R: Resume selected task
            
            <b>Commands:</b>
            • quit: Exit program
            • clear: Clear log
            • help: Show this help
            • list: List all tasks
            • cancel [id]: Cancel task by index
            • resume [id]: Resume task by index
        """)
        self.help_window = Frame(
            Window(
                content=FormattedTextControl(self.help_text),
                wrap_lines=True,
                align=WindowAlign.LEFT,
            ),
            title="Help",
        )

    def create_layout(self) -> Layout:
        """Create the main application layout."""
        return Layout(
            HSplit([
                # Upper section: Status and Details
                VSplit([
                    # Left side: Task Status
                    self.task_status_window,
                    # Right side: Selected Task Details
                    self.details_window,
                ]),
                # Middle section: Log Messages
                self.log_window,
                # Lower section: Help and Input
                VSplit([
                    # Left side: Help
                    self.help_window,
                    # Right side: Input
                    Frame(
                        self.input_field,
                        title="Input",
                    ),
                ]),
            ])
        )

    def get_style(self) -> Style:
        """Get the UI style definitions."""
        return Style.from_dict({
            'bold': 'bold',
            'status.pending': '#5555FF',
            'status.processing': '#FFFF55',
            'status.completed': '#55FF55',
            'status.error': '#FF5555',
            'status.cancelled': '#FF55FF',
            'status.paused': '#FFAA55',
            'warning': '#FFAA55',
            'info': '#55FFFF',
            'selected': 'reverse',
            'title': 'bold underline',
            'help': 'italic',
            'input-field': 'bg:#000044 #ffffff',
        })

    def format_size(self, size: int) -> str:
        """Format byte size to human readable format."""
        return humanize.naturalsize(size)

    def format_time(self, seconds: float) -> str:
        """Format seconds to human readable time."""
        return humanize.naturaldelta(seconds)

    def get_selected_task(self) -> Optional[TranscriptionTask]:
        """Get currently selected task."""
        if self.manager.tasks:
            return self.manager.tasks[self.selected_task_index]
        return None


    def format_task_status(self, task: TranscriptionTask, idx: int) -> list:
        """Format task status for display."""
        is_selected = idx == self.selected_task_index
        style_prefix = 'class:selected ' if is_selected else ''
        
        status_style = {
            TaskStatus.PENDING: 'status.pending',
            TaskStatus.DOWNLOADING: 'status.processing',
            TaskStatus.SPLITTING: 'status.processing',
            TaskStatus.COMPLETED: 'status.completed',
            TaskStatus.FAILED: 'status.error',
            TaskStatus.CANCELLED: 'status.cancelled',
            TaskStatus.PAUSED: 'status.paused',
        }.get(task.status, 'status.error')

        progress = f"{task.stats.progress:.1f}%" if task.stats.progress else "N/A"
        title = task.title if task.title else 'Unknown'
        if len(title) > 40:
            title = title[:37] + "..."

        return [
            (f'{style_prefix}class:{status_style}',
            f"{'→ ' if is_selected else '# '} {idx} | {title}\n"),
            (f'{style_prefix}class:{status_style}',
            f"    Status: {task.status.value} | Progress: {progress}\n"),
        ]


    def format_task_details(self, task: TranscriptionTask) -> list:
        """Format detailed task information."""
        if not task:
            return [('class:info', 'No task selected')]

        details = [
            ('class:title', f"Task Details - {task.id}\n\n"),
            ('', f"Title: {task.title}\n"),
            ('', f"Status: {task.status.value}\n"),
            ('', f"Progress: {task.stats.progress:.1f}%\n"),
            ('', f"Created: {humanize.naturaltime(task.created_at)}\n"),
        ]

        if task.status == TaskStatus.DOWNLOADING:
            details.extend([
                ('', f"Speed: {self.format_size(task.stats.speed)}/s\n"),
                ('', f"ETA: {self.format_time(task.stats.eta)}\n"),
                ('', f"Downloaded: {self.format_size(task.stats.downloaded_bytes)} / {self.format_size(task.stats.total_bytes)}\n"),
            ])

        if task.error:
            details.extend([
                ('class:status.error', f"\nError: {task.error}\n"),
            ])

        if task.video_metadata:
            vm = task.video_metadata
            details.extend([
                ('class:title', "\nVideo Information:\n"),
                ('', f"Channel: {vm.get('uploader') or 'Unknown'}\n"),
                ('', f"Views: {humanize.intword(vm.get('view_count') or 0)}\n"),
                ('', f"Likes: {humanize.intword(vm.get('like_count') or 0)}\n"),
            ])

        return details

    def handle_input(self, buffer) -> None:
        """Handle user input commands."""
        text = buffer.text.strip()
        
        if not text:
            return  # Ignore empty input

        command, *args = text.split()
        command = command.lower()

        if command == 'quit':
            self.stop_event.set()
            self.app.exit()

        elif command == 'clear':
            with self.history_lock:
                self.message_history.clear()
            self.update_log_display()
            self.log_message(('class:info', "Log cleared."))

        elif command == 'help':
            self.log_message(('class:help', str(self.help_text)))

        elif command == 'list':
            if not self.manager.tasks:
                self.log_message(('class:info', "No active tasks."))
            else:
                for idx, task in enumerate(self.manager.tasks):
                    self.log_message(self.format_task_status(task, idx))

        elif command == 'cancel':
            if not args:
                self.log_message(('class:status.error', "Usage: cancel [task_id]"))
            else:
                try:
                    idx = int(args[0])
                    if 0 <= idx < len(self.manager.tasks):
                        task = self.manager.tasks[idx]
                        task.update_status(TaskStatus.CANCELLED)
                        self.log_message(('class:warning', f"Task cancelled: {task.title}"))
                    else:
                        self.log_message(('class:status.error', "Task index out of range."))
                except ValueError:
                    self.log_message(('class:status.error', "Invalid task index."))

        elif command == 'resume':
            if not args:
                self.log_message(('class:status.error', "Usage: resume [task_id]"))
            else:
                try:
                    idx = int(args[0])
                    if 0 <= idx < len(self.manager.tasks):
                        task = self.manager.tasks[idx]
                        if task.can_resume():
                            self.manager.resume_task(task)
                            self.log_message(('class:info', f"Resuming task: {task.title}"))
                        else:
                            self.log_message(('class:status.error', "Task cannot be resumed."))
                    else:
                        self.log_message(('class:status.error', "Task index out of range."))
                except ValueError:
                    self.log_message(('class:status.error', "Invalid task index."))

        elif re.match(r'^https?://', text):
            if self.manager.add_task(text):
                self.log_message(('class:status.completed', f"Task added for URL: {text}"))
            else:
                self.log_message(('class:status.error', f"Task for URL {text} already exists or could not be added."))
        else:
            self.log_message(('class:status.error', 'Invalid command. Type "help" for available commands.'))
        
        buffer.text = ""

    def log_message(self, message):
        """Add a message to the log."""
        timestamp = time.strftime("%H:%M:%S")
        if isinstance(message, tuple):
            formatted_message = [('','[{}] '.format(timestamp))] + [message]
        else:
            formatted_message = [('','[{}] '.format(timestamp)), ('', str(message))]
        with self.history_lock:
            self.message_history.append(formatted_message)
        self.update_log_display()

    def update_log_display(self):
        """Update the log window content."""
        formatted_lines = []
        with self.history_lock:
            for msg in self.message_history:
                formatted_lines.extend(msg)
                formatted_lines.append(('', '\n'))
        self.log_control.text = formatted_lines
        self.app.invalidate()

    def update_status_display(self):
        """Update task status and details displays."""
        while not self.stop_event.is_set():
            try:
                # Update task status display
                status_lines = []
                if not self.manager.tasks:
                    status_lines.append(('', 'No active tasks\n'))
                else:
                    for idx, task in enumerate(self.manager.tasks):
                        status_lines.extend(self.format_task_status(task, idx))
                
                self.task_status_control.text = status_lines
                
                # Update selected task details
                if selected_task := self.get_selected_task():
                    details_lines = self.format_task_details(selected_task)
                else:
                    details_lines = [('', 'No task selected')]
                
                self.details_control.text = details_lines
                
                self.app.invalidate()
                time.sleep(0.5)
                
            except Exception as e:
                self.log_message(('class:status.error', f"Error updating display: {str(e)}"))

    def run(self):
        """Run the application."""
        try:
            # Start the status update thread
            update_thread = threading.Thread(
                target=self.update_status_display,
                daemon=True
            )
            update_thread.start()
            
            # Run the application
            self.app.run()
        finally:
            self.stop_event.set()
            self.manager.shutdown()
            print("\nShutting down...")

def main():
    ui = TranscriptionUI()
    ui.run()

if __name__ == "__main__":
    main()
