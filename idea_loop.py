#!/usr/bin/env python3
"""think-tank — autonomous divergent idea generation.

Runs a generator agent (high temperature) and a reviewer agent (low temperature)
in a loop using LM Studio. Run overnight to fill a directory with genuinely
diverse ideas on any topic.

Usage:
    python idea_loop.py [task.md] [--model MODEL] [--max-ideas N]
    python idea_loop.py -p "ways to visualize sorting algorithms in Python"
    python idea_loop.py --help

Environment:
    LM_STUDIO_API_KEY   Optional. Only needed if token auth is enabled in LM Studio.
"""

import argparse
import dataclasses
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import llm
from config import Config
from tui import TUI


# ── Prompts ───────────────────────────────────────────────────────────────────

GENERATOR_SYSTEM = """\
You are a creative idea generator. Your job is to think up ONE novel approach to the given task.

You will be shown:
- The task description
- A one-line summary of each idea already generated (may be empty for the first idea)

Your goal: produce ONE new approach that is GENUINELY DIFFERENT from all prior ideas.
Do not repeat the same core library AND the same core technique as any existing idea.
Think laterally — different visual effects, different rendering paradigms, different tools.

Output EXACTLY this format (no preamble, no extra text):

TITLE: [2-5 word slug-friendly title]
SUMMARY: [one sentence, max 15 words]
APPROACH: [2-3 paragraphs describing the approach in concrete detail]
CODE_SKETCH:
```python
[key code snippet illustrating the core technique]
```"""

REVIEWER_SYSTEM = """\
You are a novelty reviewer. Decide if a proposed idea is genuinely different from all ideas already generated.

Rules:
- REJECT if the candidate uses the same core library AND the same core technique as an existing idea
- ACCEPT if it uses a different library, a different visual technique, or a meaningfully different approach
- If the ideas index is empty, always ACCEPT

Output EXACTLY one of these two formats and nothing else:

DECISION: ACCEPT
REASON: [one sentence]

DECISION: REJECT
REASON: [one sentence — name the existing idea it duplicates]"""

TASK_FORMATTER_SYSTEM = """\
You are a task formatter for think-tank, an autonomous idea generation system.

The user will describe what they want to brainstorm about in plain language.
Rewrite it as a structured task file in EXACTLY this format — no extra text:

# Task

[a clear, direct statement of the brainstorming goal]

## Constraints
- [keep ideas grounded — e.g. library restrictions, scope, format]
- [add 2–4 constraints total, as appropriate]

## Scoring (for the reviewer)
An idea is TOO SIMILAR if [specific criterion for what makes two ideas duplicates].
An idea is NOVEL ENOUGH if [specific criterion for genuine novelty]."""


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Candidate:
    title: str
    slug: str
    summary: str
    approach: str
    code_sketch: str
    raw: str


@dataclass
class LoopState:
    accepted_count: int = 0
    rejected_count: int = 0
    consecutive_failures: int = 0
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LoopState":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Slug / parsing helpers ────────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s[:50].strip("-")


def unique_slug(base_slug: str, ideas_dir: Path) -> str:
    slug = base_slug
    counter = 2
    while (ideas_dir / f"idea_{slug}.md").exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def extract_field(text: str, label: str) -> str:
    """Pull a labeled section from structured LLM output up to the next label or end."""
    pattern = rf"^{label}:\s*(.+?)(?=\n[A-Z_]{{2,}}:|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def parse_candidate(raw: str) -> Optional[Candidate]:
    title = extract_field(raw, "TITLE") or "Untitled Idea"
    summary = extract_field(raw, "SUMMARY") or "No summary provided."
    approach = extract_field(raw, "APPROACH") or ""

    code_match = re.search(r"CODE_SKETCH:\s*\n(.*)", raw, re.DOTALL)
    code_sketch = code_match.group(1).strip() if code_match else ""

    slug = slugify(title) or "unnamed-idea"
    return Candidate(title=title, slug=slug, summary=summary,
                     approach=approach, code_sketch=code_sketch, raw=raw)


def parse_review(raw: str) -> Tuple[str, str]:
    """Returns (ACCEPT|REJECT, reason)."""
    decision_match = re.search(r"DECISION:\s*(ACCEPT|REJECT)", raw, re.IGNORECASE)
    reason_match = re.search(r"REASON:\s*(.+?)$", raw, re.MULTILINE)
    decision = decision_match.group(1).upper() if decision_match else "REJECT"
    reason = reason_match.group(1).strip() if reason_match else raw.strip()
    return decision, reason


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_state(path: str) -> LoopState:
    if os.path.exists(path):
        with open(path) as f:
            return LoopState.from_dict(json.load(f))
    return LoopState()


def save_state(state: LoopState, path: str) -> None:
    state.last_updated = datetime.now().isoformat()
    with open(path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)


def read_index(path: str) -> str:
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return ""


def init_index(path: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("# Ideas Index\n\n")


def append_to_index(path: str, slug: str, summary: str) -> None:
    with open(path, "a") as f:
        f.write(f"- [{slug}]: {summary}\n")


def save_idea_file(ideas_dir: Path, candidate: Candidate) -> Path:
    ideas_dir.mkdir(parents=True, exist_ok=True)
    filepath = ideas_dir / f"idea_{candidate.slug}.md"
    now = datetime.now().isoformat(timespec="seconds")
    content = f"""\
---
title: {candidate.title}
slug: {candidate.slug}
generated: {now}
accepted: true
---

# {candidate.title}

## Summary
{candidate.summary}

## Approach
{candidate.approach}

## Code Sketch
{candidate.code_sketch}
"""
    filepath.write_text(content)
    return filepath


# ── Stop condition ────────────────────────────────────────────────────────────

_stop_requested = False


def _handle_sigint(sig, frame):
    global _stop_requested
    _stop_requested = True


def should_stop(state: LoopState, config: Config) -> Optional[str]:
    """Returns a stop reason string, or None to continue."""
    if _stop_requested:
        return "Ctrl+C"
    if state.accepted_count >= config.max_ideas:
        return f"reached max_ideas ({config.max_ideas})"
    elapsed = datetime.now() - datetime.fromisoformat(state.start_time)
    if elapsed >= timedelta(hours=config.max_runtime_hours):
        return f"reached max_runtime_hours ({config.max_runtime_hours}h)"
    if state.consecutive_failures >= config.max_consecutive_failures:
        return f"idea space saturated ({config.max_consecutive_failures} consecutive failures)"
    return None


# ── Agents ────────────────────────────────────────────────────────────────────

def generate_idea(
    task: str,
    index: str,
    config: Config,
    tui: TUI,
    rejection_context: str = "",
) -> Optional[Candidate]:
    index_section = index if index.strip() else "(none yet — this is the first idea)"
    rejection_note = ""
    if rejection_context:
        rejection_note = (
            f"\n\nPREVIOUS ATTEMPT REJECTED: {rejection_context}\n"
            "Please try a meaningfully different approach."
        )

    prompt = (
        f"## Task\n{task}\n\n"
        f"## Ideas Already Generated\n{index_section}"
        f"{rejection_note}\n\n"
        "Generate ONE new idea that is genuinely different from all existing ideas."
    )

    raw = llm.call(
        prompt=prompt,
        system=GENERATOR_SYSTEM,
        model=config.model,
        temperature=config.generator_temperature,
        base_url=config.base_url,
        timeout=config.llm_timeout_seconds,
    )
    return parse_candidate(raw)


def review_idea(
    index: str,
    candidate: Candidate,
    config: Config,
    tui: TUI,
) -> Tuple[str, str]:
    index_section = index if index.strip() else "(none yet)"
    prompt = (
        f"## Existing Ideas Index\n{index_section}\n\n"
        f"## Proposed New Idea\n"
        f"TITLE: {candidate.title}\n"
        f"SUMMARY: {candidate.summary}\n"
        f"APPROACH: {candidate.approach}\n\n"
        "Is this idea novel enough to accept?"
    )

    raw = llm.call(
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        model=config.model,
        temperature=config.reviewer_temperature,
        base_url=config.base_url,
        timeout=config.llm_timeout_seconds,
    )
    return parse_review(raw)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(config: Config) -> None:
    signal.signal(signal.SIGINT, _handle_sigint)

    if not os.path.exists(config.task_file):
        print(f"Error: task file not found: {config.task_file}", file=sys.stderr)
        sys.exit(1)

    _resolve_model(config)

    task = Path(config.task_file).read_text().strip()
    ideas_dir = Path(config.ideas_dir)
    ideas_dir.mkdir(parents=True, exist_ok=True)
    init_index(config.index_file)

    state = load_state(config.state_file)
    prior_accepted = state.accepted_count
    state.start_time = datetime.now().isoformat()  # reset clock per session

    with TUI(config) as tui:
        if prior_accepted:
            tui.log(f"Back in action — {prior_accepted} rounds already deployed.", style="dim")

        tui.set_index_lines(read_index(config.index_file).splitlines())
        tui.set_stats(state.accepted_count, state.rejected_count, state.consecutive_failures)

        stop_reason = None
        while not (stop_reason := should_stop(state, config)):
            index = read_index(config.index_file)
            tui.set_index_lines(index.splitlines())

            retries = 0
            rejection_context = ""
            accepted = False

            while retries < config.max_retries:
                attempt = f" (retry {retries})" if retries else ""
                tui.set_status("LOADING")
                tui.log(f"Loading round{attempt}...")

                try:
                    candidate = generate_idea(task, index, config, tui, rejection_context)
                except Exception as e:
                    tui.log(f"Misfire (generator): {e}", style="red")
                    retries += 1
                    continue

                if candidate is None:
                    tui.log("Round malformed — couldn't read output.", style="red")
                    retries += 1
                    continue

                tui.log(f'Round chambered: "{candidate.title}"', style="bold white")
                tui.set_status("TARGETING")
                tui.log("Targeting...")

                try:
                    decision, reason = review_idea(index, candidate, config, tui)
                except Exception as e:
                    tui.log(f"Targeting error: {e}", style="red")
                    retries += 1
                    continue

                if decision == "ACCEPT":
                    candidate.slug = unique_slug(candidate.slug, ideas_dir)
                    filepath = save_idea_file(ideas_dir, candidate)
                    append_to_index(config.index_file, candidate.slug, candidate.summary)
                    state.accepted_count += 1
                    state.consecutive_failures = 0
                    save_state(state, config.state_file)

                    tui.set_status("DEPLOYED")
                    tui.log(
                        f"Deployed -> {filepath.name}  [{state.accepted_count}/{config.max_ideas}]",
                        style="bold green",
                    )
                    tui.set_index_lines(read_index(config.index_file).splitlines())
                    tui.set_stats(state.accepted_count, state.rejected_count, state.consecutive_failures)
                    accepted = True
                    break
                else:
                    state.rejected_count += 1
                    rejection_context = reason
                    retries += 1
                    tui.set_status("MISFIRE")
                    tui.log(f"Dud: {reason}", style="yellow")
                    tui.set_stats(state.accepted_count, state.rejected_count, state.consecutive_failures)

            if not accepted:
                state.consecutive_failures += 1
                save_state(state, config.state_file)
                tui.set_status("RELOADING")
                tui.log(
                    f"Magazine empty. Reloading for {config.retry_sleep_seconds}s  "
                    f"({state.consecutive_failures}/{config.max_consecutive_failures} misfires)",
                    style="dim yellow",
                )
                if not _stop_requested:
                    time.sleep(config.retry_sleep_seconds)

        tui.set_status("STAND DOWN")
        tui.log(f"Standing down: {stop_reason}", style="dim")
        save_state(state, config.state_file)

        # Brief pause so the user can see the final state before Live exits
        time.sleep(1.5)

    # After Live exits, print a plain summary
    elapsed = datetime.now() - datetime.fromisoformat(state.start_time)
    print()
    print("=" * 60)
    print(f"Accepted : {state.accepted_count} ideas")
    print(f"Rejected : {state.rejected_count} attempts")
    print(f"Runtime  : {str(elapsed).split('.')[0]}")
    print(f"Ideas    : {config.ideas_dir}/")
    print(f"Index    : {config.index_file}")
    print(f"Stopped  : {stop_reason}")


# ── Entry point ───────────────────────────────────────────────────────────────

def _resolve_model(config: Config) -> None:
    """Auto-detect the loaded model if none is set. Exits on failure."""
    if not config.model:
        models = llm.list_models(config.base_url)
        if not models:
            print(
                f"Error: no model found at {config.base_url}/models\n"
                "Start LM Studio, load a model, and enable the local server.",
                file=sys.stderr,
            )
            sys.exit(1)
        config.model = models[0]


def format_task(raw_prompt: str, config: Config) -> str:
    """Pass a free-form description through the model to produce a structured task.md."""
    return llm.call(
        prompt=raw_prompt,
        system=TASK_FORMATTER_SYSTEM,
        model=config.model,
        temperature=0.3,
        base_url=config.base_url,
        timeout=config.llm_timeout_seconds,
    )


def main():
    parser = argparse.ArgumentParser(
        description="think-tank — autonomous divergent idea generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
environment:
  LM_STUDIO_API_KEY    Optional. Only needed if you enabled token auth in LM Studio settings.

examples:
  python idea_loop.py                                    # auto-detect loaded model
  python idea_loop.py -p "ways to sort a list in Python" # generate task.md from prompt
  python idea_loop.py -p -                               # paste multi-line prompt (Ctrl+D to finish)
  python idea_loop.py my_task.md --max-ideas 20 --max-hours 2
  python idea_loop.py --model my-model-id
  python idea_loop.py --config my_config.json
        """,
    )
    parser.add_argument(
        "task_file", nargs="?", default="task.md",
        help="Path to the task description file (default: task.md)"
    )
    parser.add_argument(
        "-p", "--prompt", metavar="TEXT",
        help='Generate task.md from a plain-language description. Pass - to read from stdin.',
    )
    parser.add_argument("--model", help="LM Studio model ID (default: auto-detect)")
    parser.add_argument("--base-url", help="LM Studio server URL (default: http://localhost:1234/v1)")
    parser.add_argument("--max-ideas", type=int, help="Maximum ideas to generate")
    parser.add_argument("--max-hours", type=float, help="Maximum runtime in hours")
    parser.add_argument("--max-retries", type=int, help="Max retries per cycle before sleeping")
    parser.add_argument("--max-failures", type=int, help="Max consecutive failures before stopping")
    parser.add_argument("--ideas-dir", help="Directory to save idea files")
    parser.add_argument(
        "--config", default="config.json",
        help="Path to JSON config file (default: config.json)"
    )

    args = parser.parse_args()
    config = Config.load(args.config)
    config.task_file = args.task_file

    if args.model:                   config.model = args.model
    if args.base_url:                config.base_url = args.base_url
    if args.max_ideas is not None:   config.max_ideas = args.max_ideas
    if args.max_hours is not None:   config.max_runtime_hours = args.max_hours
    if args.max_retries is not None: config.max_retries = args.max_retries
    if args.max_failures is not None: config.max_consecutive_failures = args.max_failures
    if args.ideas_dir:               config.ideas_dir = args.ideas_dir

    # ── Prompt → task.md ──────────────────────────────────────────────────────
    if args.prompt is not None:
        raw = args.prompt
        if raw == "-":
            print("Paste your task description, then press Ctrl+D:")
            print()
            raw = sys.stdin.read().strip()

        if not raw:
            print("Error: prompt is empty.", file=sys.stderr)
            sys.exit(1)

        _resolve_model(config)
        print(f"Formatting task with {config.model}...\n")
        formatted = format_task(raw, config)

        print("─" * 60)
        print(formatted)
        print("─" * 60)

        task_path = config.task_file
        if os.path.exists(task_path):
            answer = input(f"\n{task_path} already exists. Overwrite? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                sys.exit(0)

        Path(task_path).write_text(formatted)
        print(f"\nSaved to {task_path}")

        answer = input("Start the loop now? [Y/n] ").strip().lower()
        if answer == "n":
            sys.exit(0)
        print()

    run(config)


if __name__ == "__main__":
    main()
