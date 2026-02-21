"""Rich-based TUI for think-tank."""

import re
import shutil
from collections import deque
from datetime import datetime
from typing import Optional

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import Config


_STATUS_STYLES = {
    "ASSEMBLING": "dim white",
    "LOADING":    "bold yellow",
    "TARGETING":  "bold blue",
    "DEPLOYED":   "bold green",
    "MISFIRE":    "bold red",
    "RELOADING":  "dim yellow",
    "STAND DOWN": "bold white",
}


class TUI:
    """Live-updating terminal display for the idea generation loop."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._log: deque = deque(maxlen=500)
        self._index_lines: list[str] = []
        self._status = "ASSEMBLING"
        self._accepted = 0
        self._rejected = 0
        self._consecutive_failures = 0
        self._start_time: datetime = datetime.now()
        self._live: Optional[Live] = None
        self._console = Console()

    # ── Public state setters ──────────────────────────────────────────────────

    def set_stats(self, accepted: int, rejected: int, consecutive_failures: int) -> None:
        self._accepted = accepted
        self._rejected = rejected
        self._consecutive_failures = consecutive_failures
        self._refresh()

    def set_status(self, status: str) -> None:
        self._status = status
        self._refresh()

    def set_index_lines(self, lines: list[str]) -> None:
        self._index_lines = lines
        self._refresh()

    def log(self, msg: str, style: str = "") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append((ts, msg, style))
        self._refresh()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _elapsed(self) -> str:
        delta = datetime.now() - self._start_time
        total = int(delta.total_seconds())
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _log_panel(self, n_lines: int) -> Panel:
        recent = list(self._log)[-n_lines:]
        text = Text(overflow="fold", no_wrap=False)
        for ts, msg, style in recent:
            text.append(f" {ts} ", style="dim")
            text.append(msg + "\n", style=style if style else "white")
        return Panel(text, title="Dispatch", border_style="dim white")

    def _index_panel(self, n_lines: int) -> Panel:
        idea_entries = [l for l in self._index_lines if l.startswith("- [")]
        # Show most recent ideas at the bottom
        visible = idea_entries[-n_lines:] if len(idea_entries) > n_lines else idea_entries

        table = Table(show_header=False, box=None, expand=True, padding=(0, 1))
        table.add_column(style="bold cyan", no_wrap=True, max_width=26)
        table.add_column(style="dim", no_wrap=True)

        for line in visible:
            m = re.match(r"-\s*\[([^\]]+)\]:\s*(.+)", line)
            if m:
                slug, summary = m.group(1), m.group(2)
                table.add_row(slug[:25], summary[:55])
            else:
                table.add_row("", line)

        count = len(idea_entries)
        return Panel(
            table,
            title=f"Deployed  {count} / {self.config.max_ideas}",
            border_style="dim white",
        )

    def _render(self) -> Layout:
        term_h = shutil.get_terminal_size((120, 40)).lines
        # header=4 + stats=3 + 2 inner panel borders each side ≈ 15 overhead
        body_lines = max(4, term_h - 12)
        # Each content line in log/index panels costs ~1 row; subtract 2 for panel borders
        inner_lines = max(2, body_lines - 2)

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="stats", size=3),
            Layout(name="body"),
        )
        layout["body"].split_row(
            Layout(name="log", ratio=3),
            Layout(name="index", ratio=2),
        )

        # ── Header ────────────────────────────────────────────────────────────
        header_text = Text(justify="center")
        header_text.append("think-tank\n", style="bold white")
        header_text.append("fire ideas, not missiles  ·  ", style="dim italic")
        header_text.append(self.config.model or "(auto)", style="dim cyan")
        layout["header"].update(Panel(Align.center(header_text), style="blue", padding=(0, 2)))

        # ── Stats ─────────────────────────────────────────────────────────────
        stats_table = Table.grid(expand=True, padding=(0, 3))
        stats_table.add_column(justify="left")
        stats_table.add_column(justify="center")
        stats_table.add_column(justify="center")
        stats_table.add_column(justify="right")

        status_style = _STATUS_STYLES.get(self._status, "white")
        fail_note = (
            f"  ({self._consecutive_failures} consecutive)"
            if self._consecutive_failures > 0 else ""
        )
        stats_table.add_row(
            f"[green]Deployed  {self._accepted}[/]",
            f"[red]Misfired  {self._rejected}[/]",
            f"[blue]On mission  {self._elapsed()}[/]",
            f"[{status_style}]{self._status}{fail_note}[/]",
        )
        layout["stats"].update(Panel(Align.center(stats_table), padding=(0, 1)))

        # ── Body ──────────────────────────────────────────────────────────────
        layout["log"].update(self._log_panel(inner_lines))
        layout["index"].update(self._index_panel(inner_lines))

        return layout

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "TUI":
        self._start_time = datetime.now()
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=2,
            screen=False,
        )
        self._live.start()
        return self

    def __exit__(self, *_) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
