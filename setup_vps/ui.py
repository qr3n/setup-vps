# setup_vps/ui.py
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich import box
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from typing import Callable, Optional, Any

from setup_vps.state import StepStatus

console = Console()

# Gradient for log dimming (from bright white to dark grey)
LOG_COLORS = ["#ffffff", "#e4e4e4", "#cccccc", "#b2b2b2", "#999999", "#808080", "#666666", "#4d4d4d"]

class LiveLog:
    def __init__(self, max_lines: int = 8):
        self.lines = []
        self.max_lines = max_lines

    def add_line(self, line: str):
        if not line.strip(): return
        # Keep only the last N lines
        self.lines.append(line[:120]) # truncate long lines
        if len(self.lines) > self.max_lines:
            self.lines.pop(0)

    def __rich__(self) -> Group:
        styled_lines = []
        # Reverse to apply gradient from bottom (newest) to top (oldest)
        for i, line in enumerate(reversed(self.lines)):
            color = LOG_COLORS[i] if i < len(LOG_COLORS) else LOG_COLORS[-1]
            styled_lines.append(Text(f"  {line}", style=color))
        
        return Group(*reversed(styled_lines))

def run_with_live_logs(title: str, func: Callable[[Callable[[str], None]], Any]) -> Any:
    live_log = LiveLog()
    with Live(Group(Spinner("dots", text=Text(f" {title}", style="bold cyan")), live_log), transient=True, console=console) as live:
        def callback(line: str):
            live_log.add_line(line)
            # live.update(...) is automatic because LiveLog has __rich__
        
        return func(callback)

STATUS_ICONS = {
    StepStatus.DONE: "[green]✓[/green]",
    StepStatus.FAILED: "[red]✗[/red]",
    StepStatus.PENDING: "[dim]●[/dim]",
    StepStatus.STALE: "[yellow]⚠[/yellow]",
    StepStatus.RUNNING: "[cyan]⟳[/cyan]",
    StepStatus.SKIPPED: "[dim]–[/dim]",
}

PT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "": "ansiwhite",
})


def print_main_menu(domain: str, steps: list[tuple[str, str, StepStatus]], done: int, total: int):
    table = Table(box=box.DOUBLE_EDGE, show_header=False, border_style="cyan", expand=False)
    table.add_column(width=36)
    table.add_column(width=10, justify="right")

    for idx, (name, title, status) in enumerate(steps, 1):
        icon = STATUS_ICONS.get(status, "?")
        table.add_row(f"  [{idx}] {title}", f"{icon} {status.value}  ")

    table.add_row("", "")
    table.add_row("  [[r]] Run all pending   [[c]] Config", "")
    table.add_row("  [[v]] Verify step       [[q]] Quit", "")

    panel = Panel(
        table,
        title=f"[bold cyan] VPS Setup — {domain} [/bold cyan]",
        subtitle=f"[dim]{done}/{total} complete[/dim]",
        border_style="cyan",
    )
    console.print(panel)


def ask_main_choice() -> str:
    return prompt(
        HTML("<ansicyan><b>choice</b></ansicyan> > "),
        style=PT_STYLE,
    ).strip().lower()


def ask_step_choice(step_title: str) -> str:
    console.print(f"\n[bold]{step_title}[/bold]")
    console.print("  [r] Run    [v] Verify only    [s] Show logs    [b] Back\n")
    return prompt(HTML("<ansicyan><b>action</b></ansicyan> > "), style=PT_STYLE).strip().lower()


def ask_error_choice() -> str:
    console.print("\n  [r] Retry    [s] Skip    [q] Quit\n")
    return prompt(HTML("<ansicyan><b>choice</b></ansicyan> > "), style=PT_STYLE).strip().lower()


def ask_confirm(message: str) -> bool:
    result = prompt(HTML(f"<ansiyellow><b>{message} [yes/no]</b></ansiyellow> > "), style=PT_STYLE).strip().lower()
    return result in ("yes", "y")


def print_check_result(label: str, value: str, passed: bool):
    icon = "[green]✓[/green]" if passed else "[red]✗[/red]"
    console.print(f"  {icon} {label}: [dim]{value}[/dim]")


def print_step_header(title: str):
    console.rule(f"[bold cyan]{title}[/bold cyan]")


def print_success(msg: str):
    console.print(f"[green]✓[/green] {msg}")


def print_error(msg: str):
    console.print(f"[red]✗[/red] {msg}")


def print_warning(msg: str):
    console.print(f"[yellow]⚠[/yellow]  {msg}")


def print_info(msg: str):
    console.print(f"[dim]→[/dim] {msg}")


def print_box(title: str, content: str, style: str = "cyan"):
    console.print(Panel(content, title=title, border_style=style))
