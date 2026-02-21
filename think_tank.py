#!/usr/bin/env python3
"""think-tank — interactive CLI entry point.

Run this. Navigate from here. Everything else is an implementation detail.

Usage:
    python think_tank.py
    python think_tank.py --config my_config.json
"""

import argparse
import re
import time
from pathlib import Path

import questionary
from questionary import Style
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import llm
from config import Config
from idea_loop import (
    _resolve_model,
    format_task,
    init_index,
    load_state,
    run,
)

console = Console()

STYLE = Style([
    ("qmark",       "fg:#4fc3f7 bold"),
    ("question",    "bold"),
    ("answer",      "fg:#4fc3f7 bold"),
    ("pointer",     "fg:#4fc3f7 bold"),
    ("highlighted", "fg:#4fc3f7 bold"),
    ("selected",    "fg:#81d4fa"),
    ("instruction", "fg:#546e7a"),
    ("separator",   "fg:#546e7a"),
    ("text",        ""),
])


# ── Display helpers ───────────────────────────────────────────────────────────

def header() -> None:
    t = Text(justify="center")
    t.append("think-tank\n", style="bold white")
    t.append("fire ideas, not missiles", style="dim italic")
    console.print(Panel(Align.center(t), style="blue", padding=(1, 8)))
    console.print()


def fresh_screen() -> None:
    console.clear()
    header()


def ask(choices: list[str], message: str = "") -> str | None:
    """Single-select menu with the house style."""
    return questionary.select(message, choices=choices, style=STYLE).ask()


def confirm(message: str, default: bool = True) -> bool:
    result = questionary.confirm(message, default=default, style=STYLE).ask()
    return bool(result)


def read_multiline(prompt_text: str) -> str:
    """Paste-friendly multi-line input. Blank line to finish."""
    console.print(f"[bold]{prompt_text}[/]")
    console.print("[dim]  (press Enter on a blank line when you're done)[/]\n")
    lines: list[str] = []
    while True:
        try:
            line = input()
            if not line and lines:
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines)


# ── Screens ───────────────────────────────────────────────────────────────────

def screen_new_session(config: Config) -> None:
    fresh_screen()

    # If a task already exists, offer to keep or replace it
    task_path = Path(config.task_file)
    if task_path.exists():
        console.print(Panel(
            task_path.read_text().strip(),
            title=f"[dim]{config.task_file}[/]",
            border_style="dim",
            padding=(1, 2),
        ))
        console.print()
        action = ask(
            ["Use this task", "Replace with new prompt", "Back"],
            message="An existing task is loaded.",
        )
        if action is None or action == "Back":
            return
        if action == "Use this task":
            _start_loop(config)
            return

    # Collect prompt
    console.print()
    raw = read_multiline("What do you want to brainstorm?")
    if not raw.strip():
        console.print("\n[red]Nothing entered.[/]")
        time.sleep(1)
        return

    # Resolve model before formatting
    try:
        _resolve_model(config)
    except SystemExit:
        console.print(
            "\n[red]Could not reach LM Studio.[/] "
            "Make sure the server is running and a model is loaded."
        )
        input("\nPress Enter to go back...")
        return

    console.print(f"\n[dim]Formatting with {config.model}...[/]\n")
    try:
        formatted = format_task(raw, config)
    except Exception as e:
        console.print(f"\n[red]Error:[/] {e}")
        input("\nPress Enter to go back...")
        return

    # Preview
    console.print(Panel(
        formatted,
        title="[bold]task.md preview[/]",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()

    if not confirm("Save and use this task?"):
        return

    task_path.write_text(formatted)
    console.print(f"\n[green]Saved to {config.task_file}[/]")
    time.sleep(0.5)

    _start_loop(config)


def screen_resume(config: Config) -> None:
    fresh_screen()

    state_path = Path(config.state_file)
    if state_path.exists():
        state = load_state(config.state_file)
        t = Table.grid(padding=(0, 3))
        t.add_column(style="dim")
        t.add_column(style="white")
        t.add_row("Deployed", str(state.accepted_count))
        t.add_row("Misfired", str(state.rejected_count))
        t.add_row("Last updated", state.last_updated[:19].replace("T", "  "))
        console.print(Panel(t, title="Session state", border_style="dim", padding=(1, 2)))
        console.print()

    if not confirm("Resume the loop?"):
        return

    _start_loop(config)


def screen_view_ideas(config: Config) -> None:
    ideas_dir = Path(config.ideas_dir)
    index_path = Path(config.index_file)

    while True:
        fresh_screen()

        # Parse the index
        entries: list[tuple[str, str]] = []
        if index_path.exists():
            for line in index_path.read_text().splitlines():
                m = re.match(r"-\s*\[([^\]]+)\]:\s*(.+)", line)
                if m:
                    entries.append((m.group(1), m.group(2)))

        if not entries:
            console.print("[dim]No ideas deployed yet.[/]")
            input("\nPress Enter to go back...")
            return

        choices = [f"[{slug}]  {summary[:65]}" for slug, summary in entries]
        choices.append("Back")

        selection = ask(
            choices,
            message=f"{len(entries)} ideas deployed — select one to read:",
        )
        if selection is None or selection == "Back":
            return

        idx = choices.index(selection)
        slug = entries[idx][0]
        idea_file = ideas_dir / f"idea_{slug}.md"

        fresh_screen()
        if idea_file.exists():
            console.print(Panel(
                idea_file.read_text().strip(),
                title=f"[bold cyan]{slug}[/]",
                border_style="cyan",
                padding=(1, 2),
            ))
        else:
            console.print(f"[red]File not found:[/] {idea_file}")

        input("\nPress Enter to go back...")


def screen_settings(config: Config) -> Config:
    while True:
        fresh_screen()

        t = Table(show_header=False, box=None, padding=(0, 3))
        t.add_column(style="dim", min_width=26)
        t.add_column(style="cyan")
        t.add_row("model",                  config.model or "(auto-detect)")
        t.add_row("base_url",               config.base_url)
        t.add_row("generator_temperature",  str(config.generator_temperature))
        t.add_row("reviewer_temperature",   str(config.reviewer_temperature))
        t.add_row("max_ideas",              str(config.max_ideas))
        t.add_row("max_runtime_hours",      str(config.max_runtime_hours))
        t.add_row("max_retries",            str(config.max_retries))
        t.add_row("retry_sleep_seconds",    str(config.retry_sleep_seconds))
        console.print(Panel(t, title="Settings", border_style="dim", padding=(1, 2)))
        console.print()

        action = ask([
            "Change model",
            "Change generator temperature",
            "Change reviewer temperature",
            "Change max ideas",
            "Change max runtime hours",
            "Save to config.json",
            "Back",
        ])

        if action is None or action == "Back":
            return config

        if action == "Save to config.json":
            config.save("config.json")
            console.print("\n[green]Saved.[/]")
            time.sleep(1)
            continue

        if action == "Change model":
            console.print("[dim]Fetching models from LM Studio...[/]")
            models = llm.list_models(config.base_url)
            if models:
                choices = models + ["(auto-detect)", "Cancel"]
                picked = ask(choices, message="Select model:")
                if picked and picked != "Cancel":
                    config.model = "" if picked == "(auto-detect)" else picked
            else:
                val = questionary.text(
                    "Model ID (LM Studio unreachable, enter manually):",
                    default=config.model,
                    style=STYLE,
                ).ask()
                if val is not None:
                    config.model = val
            continue

        # Numeric fields
        field_map = {
            "Change generator temperature": ("generator_temperature", float),
            "Change reviewer temperature":  ("reviewer_temperature",  float),
            "Change max ideas":             ("max_ideas",             int),
            "Change max runtime hours":     ("max_runtime_hours",     float),
        }
        if action in field_map:
            attr, cast = field_map[action]
            val = questionary.text(
                f"{attr}:",
                default=str(getattr(config, attr)),
                style=STYLE,
            ).ask()
            if val:
                try:
                    setattr(config, attr, cast(val))
                except ValueError:
                    console.print("[red]Invalid value.[/]")
                    time.sleep(1)

    return config


# ── Loop launcher ─────────────────────────────────────────────────────────────

def _start_loop(config: Config) -> None:
    console.print()
    try:
        run(config)
    except Exception as e:
        console.print(f"\n[red]Loop error:[/] {e}")
    input("\nPress Enter to return to the menu...")


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu(config: Config) -> None:
    while True:
        fresh_screen()

        task_exists  = Path(config.task_file).exists()
        state_path   = Path(config.state_file)
        ideas_dir    = Path(config.ideas_dir)
        has_session  = state_path.exists() and load_state(config.state_file).accepted_count > 0
        idea_files   = list(ideas_dir.glob("idea_*.md")) if ideas_dir.exists() else []

        choices: list[str] = []

        if has_session:
            state = load_state(config.state_file)
            choices.append(
                f"Resume  —  {state.accepted_count} deployed, {state.rejected_count} misfired"
            )
        choices.append("New session")

        if idea_files:
            choices.append(f"View ideas  —  {len(idea_files)} deployed")

        choices.append("Settings")
        choices.append("Quit")

        action = ask(choices)

        if action is None or action == "Quit":
            console.print("\n[dim]Standing down.[/]\n")
            break
        elif action == "New session":
            screen_new_session(config)
        elif action and action.startswith("Resume"):
            if task_exists:
                screen_resume(config)
            else:
                console.print("\n[red]No task.md found.[/] Start a new session first.")
                time.sleep(2)
        elif action and action.startswith("View ideas"):
            screen_view_ideas(config)
        elif action == "Settings":
            config = screen_settings(config)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="think-tank — interactive CLI")
    parser.add_argument(
        "--config", default="config.json",
        help="Path to config file (default: config.json)"
    )
    args = parser.parse_args()
    config = Config.load(args.config)
    main_menu(config)


if __name__ == "__main__":
    main()
