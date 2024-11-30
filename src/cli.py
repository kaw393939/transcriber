from __future__ import annotations
import sys
from pathlib import Path
import threading
import time
from typing import List

# Dynamically add project root to PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from models.tasks import TranscriptionTask, TaskStatus
from transcription.manager import TranscriptionManager

from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.widgets import TextArea, Frame
from prompt_toolkit.styles import Style

class SimpleTranscriptionUI:
    def __init__(self):
        self.manager = TranscriptionManager()
        self.kb = KeyBindings()
        self.stop_event = threading.Event()
        
        # Create display
        self.status_control = FormattedTextControl(text=[])
        self.status_window = Frame(
            Window(content=self.status_control, wrap_lines=True),
            title="Tasks"
        )
        
        # Create input
        self.input_field = TextArea(
            height=1,
            prompt='Enter URL (q=quit): ',
            multiline=False,
        )
        self.input_field.accept_handler = self.handle_input

        # Create app
        self.app = Application(
            layout=Layout(HSplit([
                self.status_window,
                Frame(self.input_field, height=3)
            ])),
            key_bindings=self.kb,
            full_screen=True,
            style=self.get_style()
        )

        # Setup quit command
        @self.kb.add('c-c', eager=True)
        @self.kb.add('c-q', eager=True)
        def _(event):
            self.stop_event.set()
            event.app.exit()

        # Start update thread
        self.update_thread = threading.Thread(
            target=self.update_display,
            daemon=True
        )
        self.update_thread.start()

    def get_style(self) -> Style:
        return Style.from_dict({
            'status.error': '#ff0000',
            'status.success': '#00ff00',
            'status.processing': '#ffff00',
            'frame.border': '#444444',
        })

    def format_task_status(self, task: TranscriptionTask) -> List[tuple[str, str]]:
        # Get appropriate style
        style = 'status.'
        if task.status == TaskStatus.FAILED:
            style += 'error'
        elif task.status == TaskStatus.COMPLETED:
            style += 'success'
        elif task.status in (TaskStatus.DOWNLOADING, TaskStatus.SPLITTING, TaskStatus.TRANSCRIBING):
            style += 'processing'
        else:
            style = ''
        
        # Format the status line
        progress = f"{task.stats.progress:.1f}%" if task.stats and task.stats.progress else "0%"
        title = task.title or task.url[:50]
        
        return [
            (f'class:{style}', f"{title}\n"),
            (f'class:{style}', f"Status: {task.status.value} | Progress: {progress}\n"),
            ('', '\n')
        ]

    def handle_input(self, buffer) -> None:
        text = buffer.text.strip()
        if not text:
            return
            
        if text.lower() in ('q', 'quit', 'exit'):
            self.stop_event.set()
            self.app.exit()
        elif text.startswith('http'):
            self.manager.add_task(text)
        
        buffer.text = ""

    def update_display(self) -> None:
        while not self.stop_event.is_set():
            try:
                # Format status display
                lines = []
                if not self.manager.tasks:
                    lines = [('', 'No active tasks\n')]
                else:
                    for task in self.manager.tasks:
                        lines.extend(self.format_task_status(task))
                
                # Update display
                self.status_control.text = lines
                self.app.invalidate()
                
                # Wait before next update
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Display update error: {e}")

    def run(self) -> None:
        try:
            self.app.run()
        except Exception as e:
            print(f"Application error: {e}")
        finally:
            self.stop_event.set()
            self.manager.shutdown()

def main():
    try:
        ui = SimpleTranscriptionUI()
        ui.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()