# TinScope Agent

TinScope is the Altoids cyberdeck network field agent. It is designed for the tiny tin display: one state, one prompt, a short inbox, and an inspection overlay for details.

The UI follows the project design system: Teenage Engineering restraint, Evangelion/MAGI command language, and VFD-style readouts. It avoids dense tables on the small screen.

TinScope performs research and visibility checks only. It does not run exploit payloads, credential attacks, deauth, file extraction, stealth actions, persistence on other hosts, or destructive checks.

## Open TinScope

Use `CMD+R` from anywhere in the Altoids UI.

TinScope is also in the normal screen cycle:

```text
Home -> TinScope -> Game -> Terminal -> System
```

## Operator Flow

TinScope runs the **Network Field Kit** mission:

1. Reads Wi-Fi status and local IP.
2. Infers a likely gateway and local network identity.
3. Runs a bounded local baseline probe.
4. Checks common gateway/web surfaces when visible.
5. Compares the current snapshot to previous memory.
6. Requests approval before a deeper local sweep.
7. Writes the latest JSON snapshot, timeline events, and Markdown report.

The main screen uses compact states:

```text
IDLE
SURVEYING
ANALYZING
REQUEST
READY
ERROR
```

## Keyboard Controls

Everything is reachable from the keyboard.

| Key | Action |
| --- | --- |
| `Enter` | Start mission, approve request, inspect selected item, or run selected action |
| `Space` | Show context for the current request/result |
| `Esc` | Deny request, close overlay, or return home |
| `Q` | Return home when no overlay is open |
| `Left` / `Right` | Switch operator page |
| `Up` / `Down` | Select page item |
| `Tab` | Inspect selected item |

In the inspection overlay:

| Key | Action |
| --- | --- |
| `Up` / `Down` | Scroll detail lines |
| `Left` / `Right` | Inspect previous or next inbox item |
| `Home` / `End` | Jump to start or end of detail |
| `Enter` / `Esc` | Close overlay |

Button controls mirror the keyboard path:

| Button | Action |
| --- | --- |
| `A` / `B` | Select previous or next inbox item |
| `X` | Start, approve, or inspect |
| `Y` | Context |
| long `Y` | Home |

## Operator Pages

TinScope keeps the autonomous agent as the default, but exposes focused manual pages for inspection and safe research actions.

| Page | Purpose |
| --- | --- |
| `Agent` | Autonomous state, current prompt, and recent inbox |
| `Map` | ASCII topology summary from the latest snapshot |
| `Inbox` | Findings, requests, and reports |
| `Targets` | Discovered hosts with compact open-port summaries |
| `Actions` | Manual safe actions like quick sweep, router check, compare, and export |
| `Timeline` | Persisted network memory events |
| `Signal` | Wi-Fi/IP/gateway instrument readout |

The Map page is intentionally visual instead of detailed:

```text
     INTERNET ?
         |
   [ROUTER] .1
     /   |   \
[DECK] .42
[HOST] .24 *22,80
```

The Actions page currently exposes:

- `QUICK SWEEP`
- `DEEP SWEEP`
- `CHECK ROUTER`
- `COMPARE LAST`
- `EXPORT REPORT`

## Persistence

TinScope stores state under:

```text
.runtime/tinscope/
```

Files:

```text
.runtime/tinscope/state.json
.runtime/tinscope/networks/<network_id>/timeline.jsonl
.runtime/tinscope/networks/<network_id>/latest.json
.runtime/tinscope/networks/<network_id>/report.md
```

`state.json` lets the UI resume the last visible inbox and report path. Network folders keep a timeline grouped by network identity.

The network identity is chosen from available context:

- Wi-Fi SSID when connected
- inferred gateway/LAN label when no SSID is available
- local IP fallback
- `offline` fallback

## Inbox And Overlay

TinScope shows findings and requests as a small inbox, similar to the `cdx` feed/reader model:

- inbox entries are short and selectable
- long details are hidden from the main display
- `Enter` or `Tab` opens the selected item
- the overlay wraps and scrolls detail text

Typical inbox entries:

```text
[#] Network field kit started
[+] LAN 192.168.1.24
[+] 3 hosts seen, 4 ports open
[?] Approve deeper local sweep?
[+] Report ready
```

## Reports

The Markdown report summarizes the latest mission:

- network identity
- SSID/local IP/gateway
- mission findings
- discovered hosts
- open ports seen by TinScope

The JSON snapshot is intended for future comparisons and automation.
