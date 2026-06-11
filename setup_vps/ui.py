# setup_vps/ui.py
from rich.console import Console
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
from typing import Callable, Optional

from setup_vps.state import StepStatus

console = Console()

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
    table.add_row("  [r] Run all pending   [c] Config", "")
    table.add_row("  [v] Verify step       [q] Quit", "")

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
