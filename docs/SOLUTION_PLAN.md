# LineBridge Solution Plan

## 1. Problem statement (as given)

All CNC-based assembly automation equipment used in electronics circuit assembly generates a data record of each action and stores it in CSV format in a particular folder. Integrated factories usually run a third-party MES of a different make, and each make uses different protocols to connect with each machine. They may also need each machine's output in a format other than CSV.

1. Generated CSV files are stored in a folder one by one, continuously.
2. The generated CSV file must be pulled from the folder.
3. The CSV contains many kinds of data; particular tables must be extracted and a new file prepared, optionally in JSON, CSV or TXT.
4. The separated table data is written in the selected format. Table selection is the user's choice, e.g. L1 x M8.
5. Up to 10 selection slots in the software; selected data goes into the file. Two of the inputs are manual entries, included in the same file.
6. The result is saved two ways: copied to a shared folder on the PC, and sent directly to the end user's site by API using a link. The link entry is a separate user setting.
7. Finally, extracted source files are saved to a separate back-up folder so the next incoming files can be used.

## 2. Users

| User | Needs |
|---|---|
| **Process / automation engineer** | Sets up the station once: folders, which tables, format, destination. Needs to *see* the result before going live |
| **Line operator** | Types Operator / Shift / Work order; needs a clear "it's working" signal |
| **MES / IT team at the customer** | Receives clean, predictable files on their channel; wants proof of what was sent |
| **Quality / traceability** | Wants an audit trail for every board |

## 3. Design principles

1. **Don't touch the machine.** The CSV it already writes is the interface.
2. **Run anywhere on the floor.** Standard-library Python only; offline; Windows first.
3. **Never lose, never duplicate.** Store-and-forward delivery plus content fingerprinting.
4. **Configure, don't code.** Every difference between customers is a setting.
5. **Show before you send.** Live preview on a real file.

## 4. Requirement → design

| # | Requirement | Design decision | Where |
|---|---|---|---|
| 1 | Files arrive one by one, continuously | Polling watcher (works on network drives where OS file events are unreliable); files processed oldest first; **stability window** so half-written files are skipped | `Engine.pending_files` |
| 2 | Pull the file from the folder | Read with BOM and odd-byte tolerance; auto-detect `,` `;` tab | `read_rows` |
| 3 | Extract tables, output JSON / CSV / TXT | Parser splits the file into titled tables and records each table's cell range; three renderers, CSV with *sections* or *flat* layout | `split_tables`, `render` |
| 4 | User picks table, e.g. "L1 x M8" | Phrase is ambiguous (Line 1 × Machine 8 vs. cell block L1:M8), so **both** are supported: name match first, then Excel-style range; plus wildcards and `[col,col]` filter | `select`, `range_table` |
| 5 | 10 selection slots + 2 manual inputs | 10 selector boxes; 2 label/value pairs written into the output metadata (JSON object, CSV `#manual:` lines or flat columns, TXT header lines) | UI §3–4, `manual_dict` |
| 6 | Shared folder + API, link entered separately | Two independent destinations with on/off, test buttons, bearer token, raw or multipart body; **outbox + retry** when either is down | `deliver`, `retry_outbox` |
| 7 | Used files moved to back-up | Moved to `backup/YYYY-MM-DD/` only after the output exists; failures → `_failed`, duplicates → `_duplicates` | `process_file` |
| — | (added) Proof of delivery | SQLite ledger: hash, time, tables, output, per-destination status | `Ledger` |
| — | (added) Easy setup | Local web control panel with preview, history, live log, folder browser | `webui.py`, `static/index.html` |

## 5. Architecture

```
┌──────────────── Machine PC ──────────────────────────────────────────────┐
│  Machine software ──writes──► input folder (*.csv)                        │
│                                   │                                       │
│  LineBridge engine (thread) ◄─────┘                                       │
│    detect → hash → parse → select → enrich → render → deliver → archive   │
│        │                                            │          │          │
│        └──► SQLite ledger        output/ + _outbox/ │      backup/        │
│                                                     │                     │
│  Control panel (http://localhost:8600) ◄── reads status, edits config.json│
└─────────────────────────────────────────────────────┼─────────────────────┘
                                ┌──────────────────────┴───────────────┐
                    \\MES-SERVER\inbox (SMB)          https://customer/api (POST)
```

- **Engine** (`core.py`): pure functions for parsing and rendering (easy to test) plus an `Engine` class for the stateful parts.
- **Control panel** (`webui.py`): `ThreadingHTTPServer` with a JSON API; the page polls status every 1.5 s. Bound to localhost by default.
- **Storage**: `config.json` (settings), SQLite ledger (history), plain folders (outbox, backup). All are human-inspectable.

## 6. Key decisions and trade-offs

| Decision | Alternative | Why |
|---|---|---|
| Python standard library only | Flask/React/pandas | Factory PCs are often offline and locked down; zero install wins |
| Polling, not filesystem events | watchdog / inotify | Events are unreliable on SMB shares and with some machine software; 2–3 s latency is fine for MES |
| Web panel instead of desktop GUI | Tkinter / WinForms | Looks modern, works from any browser, testable, can be opened remotely if allowed |
| Move source only **after** output is written | Move first | A crash mid-processing leaves the file in place to be retried, not lost |
| Outbox per destination | Fail the whole file | Shared folder can succeed while the API is down; each catches up independently |
| SHA-256 for duplicates | File name | Machines reuse or append to names; content is the truth |

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Machine CSV layouts vary between vendors | Two selection styles (named tables and cell ranges) cover most layouts; preview shows exactly what's detected; the parser is one isolated function to extend per vendor |
| MES expects a very specific schema | Flat CSV covers most import wizards; field mapping is next on the roadmap |
| Panel exposed on the network | Localhost by default; `--host 0.0.0.0` is opt-in; login is on the roadmap |
| Disk fills with backups | Dated folders make cleanup policies trivial (e.g. delete > 90 days) |

## 8. Build plan (hackathon)

| Phase | Output | Status |
|---|---|---|
| 1. Core engine | Parse / select / render / deliver / archive | Done |
| 2. Reliability | Stability window, outbox retry, duplicate guard, atomic writes, ledger | Done |
| 3. Control panel | Setup, preview, history, live log | Done |
| 4. Demo kit | Machine simulator, mock MES with outage switch, one-command demo | Done |
| 5. Tests | 19 automated tests | Done |
| 6. Next | CFX publisher, machine profiles, field mapping, fleet view | Roadmap |

## 9. Success measures for a pilot

- Time to onboard a new machine/MES pair (target: under 30 minutes, versus a custom integration project)
- % of boards with machine data delivered to MES (target: 100%, verifiable in the ledger)
- Duplicate or missing files reaching the MES (target: 0)
- Operator time spent on file handling per shift (target: none)
