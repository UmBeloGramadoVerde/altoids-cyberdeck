# `cdx`

`cdx` is a feed-first Codex client for the cyberdeck.

It does not scrape the interactive Codex terminal and it does not bind itself to rollout files by guessing. It talks to `codex app-server` over stdio, owns a real `threadId`, and renders the session as a small live transcript.

The runtime lives in [altoids/cdx.py](/home/kayna/altoids-cyberdeck/altoids/cdx.py:1). The launcher script is [config/cdx](/home/kayna/altoids-cyberdeck/config/cdx:1).

## Operator Model

`cdx` is a structured client, not a passthrough terminal.

It starts `codex app-server --listen stdio://`, then:

- initializes the JSON protocol connection
- starts a new thread or resumes an existing one
- sends messages with `turn/start`
- steers an active turn with `turn/steer`
- handles approvals as protocol requests instead of terminal keystroke injection

The result is explicit session binding and a cleaner live event stream.

## Starting `cdx`

From the tmux shell on the deck:

```bash
cdx
```

Useful flags:

```bash
cdx --cwd /path/to/project
cdx --thread-id <thread-id>
cdx --codex-bin /path/to/codex
cdx --home-override /path/to/home
cdx --xdg-state-home /path/to/state-home
```

Notes:

- `--thread-id` skips the startup picker and resumes that exact thread.
- `--home-override` and `--xdg-state-home` are mainly for controlled environments where Codex state must live somewhere specific.

## Startup Flow

On launch, `cdx` opens a small startup screen:

- `new thread`
- a list of recent threads in the current cwd

Controls:

- `Up` / `Down`: move selection
- `Enter`: start or resume
- `q`: quit

Resume is explicit. `cdx` uses `thread/resume` and binds to the returned `thread.id`. It does not guess based on “latest session in this cwd.”

## Session Controls

In the main session view:

- `Tab`: switch between feed focus and composer focus
- `Enter` in feed focus: open the selected entry in the reader
- `Enter` in composer focus: send the composer text
- `Up` / `Down` in feed focus: move between feed entries
- `PageUp` / `PageDown` in feed focus: jump through the feed
- `Home` / `End` in feed focus: jump to the first or last entry
- `Esc` in feed focus: quit
- `Esc` in composer focus: clear the composer, or return to feed focus if already empty
- `Left` / `Right` in composer focus: move through the composer text
- `Home` / `End` in composer focus: jump to the start or end of the composer
- `Backspace` / `Delete` in composer focus: edit at the cursor
- `Ctrl+L`: refresh recent thread metadata on demand
- `1`: approve the pending request
- `2`: approve for session
- `3`: reject the pending request
- `4`: reject, then leave the composer ready for a redirect message

In the reader overlay:

- `Up` / `Down`: scroll by line
- `PageUp` / `PageDown`: scroll by page
- `Home` / `End`: jump to start or end
- `Enter` or `Esc`: close the reader and return to the feed

Composer behavior:

- if there is no active turn, `Enter` sends `turn/start`
- if there is an active turn, `Enter` sends `turn/steer`

If the active turn cannot be steered, `cdx` keeps the typed text and shows the server error inline.

## Approval Flow

Approvals are first-class app-server requests.

Current supported approval types:

- command execution approval
- file change approval

Current actions:

- `1`: `accept`
- `2`: `acceptForSession`
- `3`: `decline`
- `4`: `decline`, then prepare a redirect message

`cdx` keeps the approval visible until the server confirms `serverRequest/resolved`.

## Feed Design

The feed is the main product. The rest of the UI stays thin.

Layout:

- one small header
- one temporary notice or approval line
- one dominant chronological feed with entry selection
- one bottom composer

Longer entries are clamped in the feed and expanded in the reader. Short operational events stay compact inline.

This should read like a tiny agent transcript, not a control panel.

## Icon System

Each feed line uses a small ASCII marker:

- `:>` user message
- `<:` assistant response
- `<~` assistant commentary or progress
- `[#]` tool or command in progress
- `[+]` completed tool or successful result
- `[!]` failure or blocked result

The goal is quick comprehension:

- `:> fix the binding`
- `[#] git status`
- `<~ updating the launcher`
- `<: the binding is now explicit`

## Data Model

`cdx` builds its UI from app-server events, not terminal scraping.

Main protocol pieces:

- `initialize`
- `thread/list`
- `thread/start`
- `thread/resume`
- `turn/start`
- `turn/steer`
- `item/started`
- `item/completed`
- `item/agentMessage/delta`
- `item/commandExecution/outputDelta`
- `item/fileChange/outputDelta`
- `serverRequest/resolved`

Feed entries are normalized into a small local model so the renderer only cares about:

- who is acting
- what changed
- whether it is in progress, complete, or failed

## Robustness Notes

Why this is stronger than the earlier wrapper:

- binding is by exact `threadId`
- resume is explicit
- approvals are protocol responses, not guessed terminal input
- the feed is driven by structured events
- there is no “latest rollout in this cwd” heuristic

What still is not complete:

- unsupported server request types are surfaced as notices, not full UI flows
- the UI is still terminal-native `curses`
- there is no raw CLI passthrough mode inside `cdx`

## Updating It

Deploy with:

```bash
make runtime-sync
```

or:

```bash
make update
```

That installs [config/cdx](/home/kayna/altoids-cyberdeck/config/cdx:1) into `/opt/altoids/runtime/bin/cdx`.
