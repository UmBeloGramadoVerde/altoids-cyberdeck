# TinScope

TinScope is a compact research surface for checking what a host exposes from the cyberdeck UI. It is intentionally focused on discovery and configuration visibility: DNS resolution, common TCP port reachability, HTTP response headers, TLS certificate metadata, and a Markdown report.

It does not run exploit payloads, credential attacks, stealth behavior, persistence, or destructive checks.

## Open TinScope

Use `CMD+R` from anywhere in the Altoids UI.

TinScope is also part of the normal screen cycle:

```text
Home -> TinScope -> Game -> Terminal -> System
```

## Keyboard Flow

The full TinScope flow is reachable from the keyboard with simple controls:

| Key | Action |
| --- | --- |
| Text keys | Type/edit the target host |
| `Backspace` | Delete one target character |
| `Delete` | Clear the target |
| `Up` / `Down` | Cycle target presets |
| `Left` / `Right` | Move between TinScope pages |
| `Tab` | Next page |
| `1`..`4` | Jump to Target, Ports, Web, or Report |
| `Space` | Change scan profile |
| `Space` on Report page | Export Markdown report |
| `Enter` | Run scan |
| `Esc` / `Q` | Return home |

Button controls remain available:

| Button | Action |
| --- | --- |
| `A` | Cycle target preset |
| `B` | Next page |
| `X` | Run scan |
| long `X` | Export Markdown report |
| `Y` | Change scan profile |
| long `Y` | Return home |

## Pages

### Target

Shows the active target, scan profile, resolved IP, scan age, and a compact risk meter.

Targets are hostnames or single IP addresses. CIDR ranges are rejected in the UI.

### Ports

Checks TCP reachability for the active profile and displays open ports from the selected set.

### Web

Checks `http://target/` and `https://target/` with a `HEAD /` request. It shows response status, missing security-header count, TLS certificate expiry, days remaining, and issuer when available.

Headers checked:

- `Strict-Transport-Security`
- `Content-Security-Policy`
- `X-Frame-Options`
- `X-Content-Type-Options`
- `Referrer-Policy`

### Report

Summarizes risk, open ports, header gaps, TLS state, and suggested next actions.

Press `Space` on the Report page, or long-press `X`, to export:

```text
.runtime/tinscope-report.md
```

## Scan Profiles

TinScope supports four built-in scan profiles:

| Profile | Purpose |
| --- | --- |
| `QUICK` | Small baseline for SSH, HTTP, HTTPS, and common dev ports |
| `WEB` | Web-focused ports and alternate HTTP/TLS ports |
| `DEV` | Common local development and datastore ports |
| `WIDE` | Broader common-service sweep while still staying lightweight |

Use `Space` to cycle profiles from any page except Report.

## Current Implementation

TinScope currently uses Python standard-library networking:

- `socket.gethostbyname` for name resolution
- TCP connect checks for port reachability
- `http.client` for HTTP/HTTPS header checks
- `ssl` for certificate metadata

Future extensions that fit the tool:

- optional `nmap` backend when installed
- DNS record details when a resolver library is available
- saved target profiles
- local-only LAN inventory mode
- richer report history
