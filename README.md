# think-tank

> *fire ideas, not missiles*

An autonomous two-agent brainstorming loop that runs overnight on your local models and wakes you up with a directory full of genuinely diverse ideas — not ten variations on the same thought.

```
┌─────────────────────────────────────────────────────────────────┐
│                          think-tank                             │
│            fire ideas, not missiles  ·  qwen2.5:14b             │
├─────────────────────────────────────────────────────────────────┤
│  Deployed  12     Misfired  4     On mission  0:22:17  TARGETING │
├──────────────────────────────┬──────────────────────────────────┤
│ Dispatch                     │ Deployed  12 / 50                │
│ 02:14:31 Loading round...    │ turtle-raycast-sun               │
│ 02:14:45 Round chambered:    │   Draw rays with turtle module   │
│          "Turtle Raycast"    │ matplotlib-polar                 │
│ 02:14:47 Targeting...        │   Animate a polar sun plot       │
│ 02:14:49 Deployed ->         │ pygame-sprite                    │
│          idea_turtle.md      │   Rotate a surface each frame    │
│ 02:15:01 Loading round...    │ tkinter-canvas                   │
│                              │   Draw arcs with after()         │
└──────────────────────────────┴──────────────────────────────────┘
```

---

## How it works

Two agents, same model, separated roles:

**Generator** (high temperature `~0.95`) — reads the task and a one-line summary of every idea already accepted. Produces one new candidate it believes is genuinely different.

**Reviewer** (low temperature `~0.2`) — reads the same index and decides: is this actually new? ACCEPT or REJECT with a reason. Strict. Consistent.

If the reviewer rejects, the generator gets the reason and tries again (up to 3 times). After 3 failures the loop sleeps briefly and starts fresh. After too many consecutive failures, the idea space is considered saturated and the loop stops.

Every accepted idea is saved as a markdown file with a human-readable slug (`idea_recursive-descent-parser.md`), and a one-liner is appended to a shared index. The index is what both agents read — keeping context small even after 100+ ideas.

```
LOADING → TARGETING → DEPLOYED ─────────────────────┐
    ↑           │                                    │
    └── MISFIRE ┘ (up to 3 retries)                 ↓
                └──(3 failures)──→ RELOADING → STAND DOWN
```

---

## Requirements

- [LM Studio](https://lmstudio.ai) with the local server enabled
- Python 3.10+
- `pip install requests rich`

---

## Installation

```bash
git clone https://github.com/Baswold/think-tank
cd think-tank
pip install -r requirements.txt
```

---

## Usage

1. Start LM Studio, load a model, and enable the local server (`localhost:1234`)
2. Edit `task.md` to describe what you want ideas about
3. Run:

```bash
# auto-detects whichever model is loaded in LM Studio
python idea_loop.py

# or specify a model and limits explicitly
python idea_loop.py --model qwen2.5:14b --max-ideas 50 --max-hours 8

# point at a different task file
python idea_loop.py my_task.md --max-ideas 20
```

Press `Ctrl+C` to stop gracefully — the current cycle finishes and state is saved.

### Reviewing results in the morning

```bash
python view_ideas.py           # index summary (one line per idea)
python view_ideas.py --full    # full text of every idea file
python view_ideas.py --state   # deployed / misfired counts, runtime
python view_ideas.py --count   # just the number
```

---

## task.md format

```markdown
# Task

Think up ways to make a spinning sun in Python.

## Constraints
- Must be runnable with standard Python libraries or pip-installable packages
- Should be a complete, self-contained approach

## Scoring (for the reviewer)
An idea is TOO SIMILAR if it uses the same core library AND the same core technique.
An idea is NOVEL ENOUGH if it uses a different library or a meaningfully different approach.
```

---

## Configuration

All defaults can be overridden in a `config.json` or via CLI flags:

| Key | Default | Description |
|-----|---------|-------------|
| `model` | *(auto-detect)* | LM Studio model ID |
| `base_url` | `http://localhost:1234/v1` | LM Studio server URL |
| `generator_temperature` | `0.95` | How creative the generator is |
| `reviewer_temperature` | `0.2` | How strict the reviewer is |
| `max_ideas` | `50` | Stop after this many deployed ideas |
| `max_runtime_hours` | `8.0` | Stop after this many hours |
| `max_retries` | `3` | Retries per cycle before sleeping |
| `max_consecutive_failures` | `10` | Saturation threshold |
| `retry_sleep_seconds` | `30` | Sleep duration after exhausting retries |

```bash
# example: conservative first run to test quality
python idea_loop.py --max-ideas 20 --max-hours 2

# example: use a config file
python idea_loop.py --config my_config.json
```

Set `LM_STUDIO_API_KEY` in your environment if you have token authentication enabled in LM Studio settings.

---

## Output structure

```
project/
├── task.md                       ← you write this
├── ideas_index.md                ← one-liner per accepted idea
├── ideas/
│   ├── idea_turtle-raycast.md
│   ├── idea_matplotlib-polar.md
│   └── ...
└── .loop_state.json              ← cycle count, stats, resume state
```

Runs are resumable — if you stop and restart, the loop picks up where it left off.

---

## Why not just ask for "50 ideas"?

A single prompt asking for many ideas produces variations on a theme. think-tank forces genuine divergence: each generation cycle sees only the index of what's already been accepted and is explicitly told to do something different. The reviewer enforces it. The result is a much wider spread across the idea space — especially after 20+ ideas, where a single prompt would start repeating itself.
