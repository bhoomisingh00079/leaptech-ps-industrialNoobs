# CNCBridge

Takes the CSV file an assembly machine writes for every board, cuts out the cell ranges you
choose, adds two operator-entered values, and delivers the result as JSON, CSV, TXT or a PDF report to a
shared folder and/or an API link. The used machine file is then moved to a back-up folder.

It is a native Windows program (`CNCBridge.exe`, about 12 MB). Nothing else has to be
installed on the machine PC and it needs no internet connection.

## Application scope in this branch

This README documents the **CNCBridge desktop application**. The desktop build includes PDF output, the single
Admin account and operator view, the searchable Materials database, and the optional authenticated YSUP-Link
receiver. SetupRequest and MaterialRequest verification remain unavailable until database-backed verification
is implemented; see [YSUP-Link receiver](docs/FEATURES.md#ysup-link-receiver) for its current behavior.

The separate [`linebridge-Simulation/`](linebridge-Simulation/README.md) folder contains an earlier browser-based
LineBridge demo and mock MES. That demo has its own README and does not currently share all of the desktop features
listed above. In particular, its output renderer supports JSON, CSV and TXT; it does not implement PDF output.

## What it does with each file

1. Watches the machine folder. A file is picked up once the machine has finished with it:
   unchanged for 2 seconds and no longer held open.
   For a machine that writes a file in bursts, set *File is complete when it contains* (**Settings > Processing...**) to the text the machine writes last, for example `End_Time`: the file is then taken
   only once it contains that text. A file that never gets it stays in the machine folder, and
   CNCBridge raises an alert after the usual time, so it is not forgotten.
   The *File pattern* may name several kinds of file, separated by `;` (for example `*.csv; *.json`).
   Files are taken oldest first, except that files fitting *Process these files first*
   (**Settings > Processing...**, for example `urgent_*.csv; rework_*`) go ahead of the others, in the
   order of those patterns.
2. Reads it (comma, semicolon, tab or `|` separated; UTF-8, UTF-16 or Windows code page). A **JSON**
   machine file is laid out as rows first, so ranges, headings and the picker work on it the same way:
   a list of records becomes a header row and a row per record; an object gives one `name, value` row
   per single value (nested names joined by `.`, such as `board.serial`), then each list of records
   under a title row `[name]`. Any other JSON is set aside as *Could not be read*.
3. Cuts out up to **10 cell ranges**, written like in a spreadsheet:

   | You enter | Meaning |
   |---|---|
   | `L1:M8` (also `L1 x M8`, `L1xM8`) | the block from cell L1 to cell M8 |
   | `B6` | one cell |
   | `A12:I*` | from A12 down to the first empty row, for tables whose length changes per board |
   | `@[ERRORS]` | the table under the cell that says `[ERRORS]`, wherever it is in the file. It runs as far right as its header row and down to the first empty row, so it still works when the tables above it grow or shrink |
   | `@NOZZLE*` | every table whose title fits (`*` any text, `?` one character), one block each, named after its title |
   | `A12:I*[Ref, Result]` | only these columns, in this order; works after any block with a header row, also `@[ERRORS][Code, Description]`. Names are matched ignoring case, by their name in the file or after *Rename columns*; a missing one is left out with a warning |

   Per range you choose a name and whether its first row is a header row.

   The picker lists the **Tables in this file**: every title (such as `[ERRORS]`) over a header row, with its
   cells, rows and columns. Click one to mark it in the grid as `@` plus its title, then **Assign**.
   In the picker, tick **Find by heading** before marking a cell: the range becomes `@` plus the text of that cell. A heading range also reports where it was found (`cells`, for example `A49:D50`) in the output. If the heading is missing from a file, that selection is empty and CNCBridge warns about it; a file where every selection is empty is set aside as failed.
4. Adds the **2 typed values** (for example Operator and Work order) plus the source file
   name, time and station name.
5. Writes one output file in the chosen format: **JSON, CSV, TXT or PDF**.
6. Delivers it to the **shared folder**, to the **API link**, or to both.
7. Moves the machine file to `back-up\YYYY-MM-DD\`, so the machine folder only ever holds new files,
   and keeps a copy of the output file beside it in `back-up\YYYY-MM-DD\outputs\` for **View output**.
   Both go when the back-up clean-up deletes that day.

<!-- M2-materials -->
## Materials database

The **Tools > Materials...** window shows the imported customer CSV as a searchable, read-only list. An Admin can
choose **Import database...** to review the column mapping, rows kept/refused and quantity warnings before
replacing the current database. **Save template CSV...** creates a blank file with the standard columns:
`Part Number, Description, Reel ID, LOT ID, Quantity`. Extra customer columns are retained. A reel ID appearing
more than once causes all rows with that reel ID to be refused; rows without a part number are also refused.
Unreadable quantities are shown as warnings and their otherwise-valid rows are kept.

The CSV is stored at `ysup_database` (by default `ysup_materials.csv` in the CNCBridge data folder), so it can
be shared with YSUP-Link. **Settings > Options > Materials** controls which Part Number, Reel ID and Lot ID
fields are enabled for YSUP lookups, optional extra customer fields, and an optional automatic import folder.
While monitoring is stopped the open CNCBridge window checks that folder periodically; while monitoring runs,
the engine checks it each cycle. A valid CSV replaces the database and moves to `imported\`. An invalid CSV is
moved to `rejected\`, raises an alert, and leaves the existing database in place. Import details and refused
rows are stored beside the CSV in `ysup_materials.refused.json`; that sidecar is ignored if the database content
is later replaced outside CNCBridge.
<!-- /M2-materials -->

<!-- M3-ysup-receiver -->
## YSUP-Link receiver

The optional YSUP-Link receiver accepts JSON POST requests at `/` or `/ysup/api` while monitoring is running.
Enable it under **Settings > Options > YSUP-Link**. It is off by default and listens on
`127.0.0.1:3001`; see [Configuration](docs/CONFIGURATION.md#ysup-link-receiver),
[Operations](docs/OPERATIONS.md#ysup-link-receiver), and [Architecture](docs/ARCHITECTURE.md#ysup-link-listener)
before exposing it to another host. No configured credentials means loopback-only access; configured credentials
require Basic Auth or a token. SetupRequest and MaterialRequest return safe “verification is not configured”
responses until M4 adds database-backed verification. The receiver does not add an API delivery format.
<!-- /M3-ysup-receiver -->

## Install

**With the set-up program.** Run `CNCBridgeSetup.exe` as an administrator (Windows asks). It installs
CNCBridge into `C:\Program Files\CNCBridge`, adds Start-menu entries and a desktop shortcut, makes the data folder
writable for ordinary users (they run CNCBridge, and it keeps its settings, history and queue there), and lists
CNCBridge under **Settings > Apps**, from where it is uninstalled (so does `Uninstall CNCBridge` in the Start menu).
Run the same set-up again to update: the program files are replaced, settings and history stay. If CNCBridge is
running, the set-up offers to close it. Uninstalling removes the program, the shortcuts and the background task and
asks whether to keep the settings and history (it does by default).

For deployment without a window: `CNCBridgeSetup.exe /S` installs, `/D=D:\CNCBridge` (last on the line) chooses the
folder, `/NOMENU` and `/NODESKTOP` leave out the shortcuts, and `CNCBridgeSetup.exe --uninstall /S` removes it
(`/DELETEDATA` also deletes settings and history). The exit code is 0 when done, 1 for a problem and 2 when CNCBridge
is running. `build_setup.bat` builds it from `dist\CNCBridge.exe`, so run `build_exe.bat` first; `release.bat` does
both and puts it in the release zip. The setup is not signed unless `CNCBRIDGE_SIGN_CERT` is set (see *Signing the
programs*). The settings can be made administrator-only (*Protecting the settings*): the typed values and shift
confirmations that operators save are then kept in files of their own, which stay writable for them.

**Or by hand.** Copy `dist\CNCBridge.exe` into a folder of its own on the machine PC, for example
`C:\CNCBridge\`, and start it. Settings, history, send queue and log are kept in the data
folder `%ProgramData%\CNCBridge` (or `%LOCALAPPDATA%\CNCBridge` if that cannot be written),
outside the program folder, so replacing or rebuilding the program never removes them.
**File > Open folder > Data folder** opens it.

Upgrading from version 1.0, which kept these in `data` beside the program: the first start
moves that folder to the new place and renames the old one to `data.moved-<date>`.

For two machine folders on one PC, give each copy its own data folder, for example a shortcut
to `CNCBridge.exe --data C:\CNCBridge\line2`. Two copies upgraded from 1.0 need nothing: the
first moves to the new place and the second keeps using its own `data` folder.

### Bundled fonts

CNCBridge bundles the official Google Fonts Roboto and Roboto Mono variable TrueType files, and IBM Plex
Sans (Regular, Medium, SemiBold) and Plex Mono (Regular, Medium) for the Dashboard theme, in `assets/fonts/`,
together with each family's SIL Open Font License (`IBMPlex-OFL.txt` for Plex). Before creating the
Tk window, the app registers both fonts privately for its own process: GDI on Windows,
Core Text on macOS, and Fontconfig on Linux. This does not install fonts system-wide and
does not require an internet connection. Linux systems need Fontconfig available for
process-local registration. If registration is unavailable, CNCBridge falls back to
matching fonts already installed on the computer. The Legacy theme uses the Windows fonts
(Segoe UI, Consolas) and does not register them.

PyInstaller builds must include the `assets/fonts` directory as application data. The
Windows `build_exe.bat` command does this automatically; the `.spec` files also include
the fonts and license files. For a direct PyInstaller CLI build on macOS or Linux, use
`--add-data "assets/fonts:assets/fonts"` (Windows uses `;` as the source/destination
separator, as in `build_exe.bat`).

## Set up

**The first-start guide.** On a new set-up the Setup tab opens with a short guide at the top, *Getting
started*: four steps (the folders, what to extract, where the files go, start), each with a tick when it
is done and a button that takes you to it: it opens the folder fields, a machine file in the range picker
(the newest one, or it asks you for one), the Delivery tab, or starts monitoring. A step loses its tick if
the setting stops being right, and a delivery shows whether its Test button has worked. The guide uses the
same rules as **Start monitoring**, so a step is ticked exactly when nothing is missing there. It goes away
after the first start, or with **Close this guide**; **View > Setup checklist** shows it again. A copy that
is already set up when it opens never shows it. The marker is the file `first_start.json` in the data
folder.

**Setup tab**

1. *Folders*: the machine folder and the back-up folder. They must be different folders.
2. *Output format*: JSON, CSV, TXT or PDF.
3. *Data to extract*: type the ranges, or press **Pick...**, which opens a real machine file
   as a grid with column letters and row numbers. Drag over the cells, choose the slot, give
   it a name, press **Assign**. The right-hand side shows the exact file that will be produced.
4. *Typed values*: two name/value pairs. A changed value applies from the next file after
   **Save settings**; monitoring does not have to be stopped.

**Delivery tab**

5. *Shared folder*: a local or network path such as `\\MES-SERVER\inbox`. **Test shared folder**
   checks that it can be written to.
6. *API link*: the address the files are POSTed to. Optional token (sent as
   `Authorization: Bearer <token>`), optional extra headers (one `Name: value` per line),
   and whether the file is sent as the request body or as a form upload (field `file`, plus
   `station`). **Test API link** sends a small test file.

Press **Save settings**, then **Start monitoring**. **Process now** handles the files that
are waiting once, without continuing to watch.

**File > Export settings...** writes the settings to a file (without the API token, which only
works on this PC), **Import settings...** reads them on another PC, and **Restore an earlier
version...** brings back one of the last 20 saved versions. Every **Save settings** keeps a copy
in `backups` in the data folder. An imported or restored version only fills the form; press
**Save settings** to keep it.

## Words used in CNCBridge

The window, Help (**Help > Words used in CNCBridge**), the messages and this guide use the same words:

| Word | Means |
|---|---|
| **Machine file** | a CSV file the machine writes. The **machine folder** is where it writes them. |
| **Back-up folder** | where a machine file is moved once it is handled, with a folder for each day. |
| **Output file** | the JSON, CSV, TXT or PDF file made from a machine file. |
| **Destination** | where an output file is sent: the **shared folder**, the **API link** or the **FTP server**. |
| **Typed values** | the two name and value pairs (for example Operator and Work order) written into every file. The engineer chooses the names, the operator changes the values. |
| **Done** | counter: machine files that were read and handled. |
| **Waiting to be read** | counter: machine files in the machine folder that have not been taken yet. |
| **Waiting to be sent** | counter: output files waiting for a destination (the send queue). |
| **Could not be read** | counter: machine files set aside in the `_failed` folder of the back-up folder. |
| **Refused** | counter: output files that a destination (API link or FTP server) refused; they are in the data folder's `rejected` folder, and **History > Send again** tries them once more. |
| **Set aside** | a file moved out of the way instead of handled: could not be read, duplicate, or refused. |

In History the delivery columns say **Delivered**, **Waiting to be sent**, **Refused** or **Off**, and the Result
column says **Done**, **Could not be read** or **Duplicate**: the same words as the counters. What is stored
in the history database stays `ok`, `queued`, `rejected (...)`, `off`, `failed`, `duplicate`.

One name is kept from version 1.0 on purpose, because receiving systems read it: a typed value that has no
name is written to the output files as `Manual input 1` or `Manual input 2`.

## The window

The **four tabs** hold the daily work: **Setup**, **Delivery**, **Pick ranges & preview** and
**History**. Under the menu bar, the toolbar has **Start monitoring**, **Stop**, **Process now** and
**Save settings**. Everything else is in the menus:

| Menu | What it holds |
|---|---|
| **File** | Open CSV file, open the newest machine file, Save settings, Export / Import / Restore settings, Open folder (machine, back-up, shared, data), Exit |
| **Monitoring** | Start monitoring, Stop, Process now |
| **Settings** | **Options...** and a direct entry for each page of it: Processing, Output, Alerts, Operators & access, Appearance. Lock / Unlock setup |
| **Tools** | Test shared folder, Test API link, Test FTP server, Send test alert, Send test heartbeat, Simulation |
| **View** | Activity log (its own window), and the four tabs |
| **Help** | Getting started and a topic for each part of the program, Keyboard shortcuts, About |

The **Options** window has one page per group of settings that are set once and rarely changed:
*Processing* (check interval, file encoding and separator, file-complete marker, duplicates,
background processing, clean-up), *Output* (the output switches), *Alerts*, *Operators & access*
(shift changes and the setup password) and *Appearance*. It has its own **Save settings** button.

**Help** explains how to use the program, in plain language, without leaving it. Menu entries
show their keyboard shortcut:

| Keys | Does |
|---|---|
| Ctrl+O / Ctrl+S | Open a CSV file / Save settings |
| F5 / Shift+F5 / F6 | Start monitoring / Stop / Process now |
| Ctrl+, | Options |
| Ctrl+L | Activity log |
| Ctrl+1 ... Ctrl+4 | Setup, Delivery, Pick ranges & preview, History |
| F1 | Help |
| Alt+F / M / S / T / V / H, F10 | Open a menu |
| Arrow keys, Enter, Esc (in a menu) | Move, choose, close; Left / Right go to the next menu |

## Output files

Named `<machine file name>_<date>_<time>.<json|csv|txt|pdf>`.

JSON:

```json
{
  "source_file": "board_00001.csv",
  "processed_at": "2026-10-10T15:38:15",
  "station": "SMT Line 1 - PNP M8",
  "manual_inputs": { "Operator": "OP-17", "Work order": "WO-1187" },
  "selections": [
    { "name": "Placements", "range": "A12:I*",
      "columns": ["Timestamp", "Ref", "Part_No"],
      "rows": [ { "Timestamp": "2026-10-09 10:00:00.581", "Ref": "R101", "Part_No": "RC0402-10K" } ] },
    { "name": "Board serial", "range": "B6", "value": "B240001" },
    { "name": "Raw block", "range": "L1:M8", "rows": [ ["a", "b"], ["c", "d"] ] }
  ]
}
```

A range with "first row is header" gives `columns` and one object per row; without it, `rows`
is a list of lists; a single cell gives `value`.

CSV: an `[INFO]` block of `key,value` lines (source file, time, station, typed values),
then for each range a line `[name],range` followed by its rows.

TXT: the same content as an aligned, readable report.

PDF: a printable report on A4 landscape pages: the file, time, station and typed values at the top,
then each range as `name: value` or as a table with its column names repeated on every page. A table
too wide for the page is printed in groups of columns; a cell too long for its column is cut short
with `...`. It is made with the standard library only and uses the PDF readers' built-in Helvetica,
so nothing is embedded and the files stay small. That font covers the Windows Western code page:
other characters (Japanese, Chinese, Cyrillic...) print as `?`, so choose JSON, CSV or TXT for such
data. The API receives it as `application/pdf`; check that the receiver accepts PDF before choosing it.
On **Pick ranges & preview** the preview then says how many pages the report has, and
**Save preview as PDF...** saves it and opens it in the PDF reader.

All text output is UTF-8. While a file is being copied into the shared folder it has a temporary
name starting with `.` and ending in `.part`; it gets its real name only when complete.

### Output switches

Four optional settings under **Settings > Output...** change how files are written. All
are off, and with all of them off the files are byte for byte what version 1.0 wrote, so a
system that already reads them keeps working. Turn one on only when the receiving system asks
for it. The Pick tab's preview shows the effect.

| Setting | Effect |
|---|---|
| JSON: write numbers as numbers | `"X_mm": "40.53"` becomes `"X_mm": 40.53`. Text, IDs with leading zeros (`00123`), numbers with a decimal comma (`12,5`) and numbers longer than 15 digits stay text |
| CSV: one plain table | one header row, then a row per record. In front of each row: `source_file`, `processed_at`, `station`, your inputs and `selection` (the name of the range). A single cell becomes a row with a `value` column; a range without a header row gets `col1`, `col2`... Data columns with the same name as a front column are called `data_<name>` |
| Add the time zone | `processed_at` becomes `2026-10-19T09:30:00+05:30`. Times copied from the machine file are not changed, because CNCBridge does not know their zone |
| Rename columns | one `Old name=New name` per line, matched ignoring upper and lower case, for example `Ref=Reference`. Applies to every selection with a header row, however it is chosen. If two columns end up with the same name, the second gets `_2` |

## FTP / FTPS delivery

A third destination, switched on under **7 FTP server** on the Delivery tab (off by default; with it off nothing
changes). Enter the server and port, a user name (empty = anonymous), the password (stored encrypted, never
exported) and a folder on the server (for example `/incoming/line1`; missing folders are created, empty = the
login folder). **Encrypt (FTPS)** uses explicit TLS for the login and the data; **Passive mode** is on, as most
firewalls need. Each file is uploaded under a temporary name starting with `.` and renamed when complete, like
the shared folder, and a name that is taken gets `_1`, `_2`... **Test FTP server** (or Tools > Test FTP server)
uploads and removes a small test file.

It uses the same queue as the other destinations: if the server is down the files wait and are sent in order
when it is back, with the usual alert. A reply that refuses one file (552 too big, 553 name not allowed) sets that
file aside in `rejected` and the others continue; **Send again** in History puts it back. A wrong login or an
unreachable server is treated as a problem with the server, not the file: everything waits. History shows an
**FTP** column. Histories from before FTP gain the column when opened.

## What the API receives

`POST <your link>` with the file as body (`Content-Type` `application/json`, `text/csv` or
`text/plain`) or as `multipart/form-data`. Headers: `X-Filename`, `X-Station`,
`Authorization` if a token is set, and your extra headers. Any 2xx answer counts as delivered.

## When something goes wrong

| Situation | What happens |
|---|---|
| Shared folder or API cannot be reached | Output files wait in `outbox` in the data folder and are sent in order when it is back. Machine files are still processed and backed up. |
| The API refuses one file (HTTP 400, 409, 413, 415, 422) | That file is moved to `rejected` in the data folder, shown in red in the header and in History, and the next files continue. |
| Wrong token or wrong link (401, 403, 404...) or a redirect | Treated as a problem with the link, not the file: everything waits in the queue until the settings are corrected. |
| A machine file has no data in any chosen range, or is not a readable CSV | Moved to `back-up\_failed`, listed as failed. |
| The same file content arrives again | Moved to `back-up\_duplicates` and not sent. Can be switched off in **Settings > Processing...**. |
| Back-up drive not available | The machine file is left where it is and nothing is sent for it until the drive is back. |
| PC restarted | Settings, history and the send queue are on disk; start the program and it continues. |

The **History** tab lists every file with its result per destination. Select rows there and press
**Send again** to put files the receiving site refused back in the send queue (once its problem is
fixed), or **Process again** to put machine files that were set aside back in the machine folder
(for example after correcting a cell range). A duplicate processed again is delivered a second time,
so CNCBridge asks first. **View output** (or a double-click on a row; on the Dashboard theme's Monitor
too) shows the output file that was made: JSON and CSV as tables, one selection at a time, with a filter
over all columns (the first 1,000 matching rows are listed); TXT as it is; a PDF with its number of pages
and **Open in PDF reader**. **Save a copy...** and **Open folder** are there as well. It shows the copy
kept in the back-up folder, or the file itself while it waits in the send queue or among the refused
files; outputs from before this version, or from a day the clean-up has deleted, are not kept.
The activity log (**View > Activity log**) and
`logs\cncbridge.log` in the data folder hold the details.

## Sending a problem to support

**Help > Export diagnostics...** saves one zip file (named like `CNCBridge diagnostics Line 7 2026-10-21.zip`)
and opens the folder with it selected. Send that one file; nobody has to ask for the log, then the settings,
then a screenshot. It holds:

| File | What |
|---|---|
| `summary.txt` | version, PC and Windows, whether the program runs in a window or in the background, the counters, the last file, the oldest file waiting to be sent, free space on the back-up drive, open problems, the last settings change |
| `settings.json` | the settings; every password, token and header value is left out and only "set" or "not set" is said |
| `history.csv` | the last 500 files and where each went |
| `logs\` | the log files, newest first, about 5 MB at most |
| `alerts.json` | the problems that are open |
| `settings_changes.jsonl` | the last 200 changes to the settings (who, when, what) |
| `files.txt` | the names, sizes and times of the files in the send queue and the refused ones, not their contents |
| `stations.txt` | the copies of CNCBridge on this PC |

Nothing in it is a password. Every text is scrubbed first: the saved passwords and header values are replaced
by `***` wherever they appear (a log line can quote one), `Authorization:` values are hidden, and a web
address loses its query part, where keys are usually sent. Reading is read-only: the history is opened
read-only, the single-copy lock is not touched and nothing in the data folder changes, so it can be exported
while monitoring runs, and also while the setup is locked.

With no window, for example in a remote session on the machine PC:
`CNCBridge.exe --diagnostics C:\Temp\diagnostics.zip` (add `--data DIR` for another copy's data folder).

## Several machines (Stations)

One copy of CNCBridge looks after one machine folder, with its own settings, history and send queue. For two
lines on one PC, start a second copy with its own data folder, for example a shortcut to
`CNCBridge.exe --data C:\CNCBridge\line2`. **View > Stations...** shows all the copies on the PC side by side:
station name, whether it is open in a window, running in the background or stopped, the counters (done,
could not be read, waiting to be read, waiting to be sent, refused), the last file, the back-up drive's free space and any open problem. Each
copy adds its data folder to `stations.json` beside the default data folder when it starts. The window only
reads the other copies (their history is opened read-only, and their lock is never touched), so it cannot disturb
them. **Open** starts CNCBridge on a copy's data folder, **Open data folder** shows it in Explorer, **Add...**
lists a folder by hand and **Remove from list** takes one off (nothing is deleted). A full multi-machine
window, with one set of settings per machine inside one program, is deliberately not built: separate copies keep
each line's settings, history and failures apart.

## The health line

Under the counters, one line shows the facts an operator checks first: **Last file** (time and how long
ago), **Oldest in queue** (how long the oldest output has waited to be delivered, or *Queue empty*) and
**Back-up drive** (free space). An item turns amber or red when it needs attention: a queued file older than the
alert time (red at three times it), a back-up drive under 2 GB or 5% free (red under 1 GB), or a machine folder
that cannot be reached. A back-up drive that stays low also raises an alert through the usual channels, once, with
a "working again" message when space is back, because a full drive stops processing.

## Heartbeat

An alert tells you when something is wrong; the heartbeat tells a monitoring tool or the MES that the
program is still alive. **Settings > Heartbeat...** switches it on and takes a web address, a folder, or both.
While monitoring runs, every few minutes (default 5) one small JSON message goes to the address (a POST,
like an alert webhook) and is written to `<station>.status.json` in the folder (written whole, so a reader
never sees half a file):

```json
{"app": "CNCBridge", "version": "1.2.0", "kind": "heartbeat", "state": "ok", "station": "Line 7",
 "computer": "LINE7-PC", "time": "2026-10-21T10:15:00", "every_minutes": 5, "problems": [],
 "counts": {"processed": 412, "failed": 1, "duplicate": 3, "waiting": 0, "queued": 0, "refused": 0},
 "health": {"last_file": "2026-10-21T10:14:12", "last_file_age": 48, "oldest_queued_age": null,
            "backup_free": 123456789012, "disk_level": "ok", "source_reachable": true}}
```

`state` is `ok`, `problem` (an alert is open; `problems` lists them), `stopped` (monitoring was stopped on
purpose, sent once as it ends) or `test` (the **Send test heartbeat** button). A copy that stops sending
**without** a `stopped` message is down: the PC is off or the program was killed. Deciding "no message for
three intervals means down" is the receiver's job. A heartbeat that cannot be sent is logged and, after the
usual alert time, announced like any other problem. It is sent on a thread of its own, so a slow receiver
never delays the files.

## Windows Event Log

Warnings and errors, and a few named events, are also written to the Windows Event Log (Application log,
source `CNCBridge`), so tools that already watch the Event Log see them without any setup:

| Event | Meaning |
|---|---|
| 1000 / 1001 | monitoring started / stopped |
| 1100 | settings changed (who and what, from the settings history) |
| 1101 | settings file edited outside CNCBridge |
| 1200–1203 | Admin login, failed login, logout, password changed |
| 2000 / 2001 | an alert was raised / cleared |
| 2100 | the heartbeat cannot be sent |
| 3000 / 3001 | any other warning / error |

The same text is written at most once every ten minutes. The set-up program registers the source (under
`HKLM\SYSTEM\CurrentControlSet\Services\EventLog\Application`) so that Windows shows the text as it is
and the uninstall removes it again. A copy that was not installed still writes the events; Windows then
adds a line saying it cannot find the description, and the text follows. In Event Viewer, look in
Windows Logs > Application for the source CNCBridge. In PowerShell:

```
Get-WinEvent -LogName Application -MaxEvents 500 | Where-Object ProviderName -eq 'CNCBridge' |
    Select-Object TimeCreated, Id, @{n='Text'; e={$_.Properties[0].Value}}
```

## Alerts

CNCBridge tells someone when it needs attention, instead of only writing it to the log:

- a problem that lasts longer than the set time (default 10 minutes): a destination that cannot
  be reached, a machine file stuck because a folder is unavailable, or incomplete settings. When
  it clears, a "working again" message follows;
- a file set aside: a machine file moved to `back-up\_failed`, or a file the API refused.

Set them up under **Settings > Alerts...**:

| Channel | Default | Needs |
|---|---|---|
| Windows notification on this PC | on | nothing |
| Webhook (JSON POST; Teams and Slack incoming webhooks accept it) | off | the link |
| Email | off | mail server, port, and the address to send to; a login if the server asks for one |

**Send test alert** sends a test through every channel that is switched on and shows the result.
While a problem is open, a red banner under the counters shows it. The email password is stored
encrypted, like the API token, and is never exported.

## Processing in the background

Tick *Process in the background* in **Settings > Processing...** and press **Save settings**. Processing
then moves to a Windows background task for your user. It goes on after the window is
closed and starts by itself when you log on to Windows (no administrator rights needed).

While the background runs, the window is *connected* to it: the header shows
**In background**, the counters, history, alerts and the activity log show the background's
work, and **Start monitoring** and **Stop** start and stop it. Settings you save are picked up
by the background within a few seconds, without a restart. **Process now** is not needed
and is greyed out.

Untick the option and press **Save settings** to go back: the background stops after the
current file, the task is removed, and the window processes files again.

Without the option, closing the window stops processing, as before. To start the window
with Windows, tick *Start monitoring when the program opens* and put a shortcut to
`CNCBridge.exe` into the Startup folder (press Win+R, type `shell:startup`).

Command line: `--headless` (no window), `--headless --once` (process what is waiting and
exit), `--data DIR` (use another data folder).

## Demo and test kit

Watch the [LineBridge web and desktop feature walkthrough](https://youtu.be/gdg7u_4Z4y8).

To show CNCBridge working without a real machine or a real MES, put `CNCBridgeDemo.exe`
in the same folder as `CNCBridge.exe` and start it. It:

- creates a folder `CNCBridge_demo` beside itself with a pretend machine folder, back-up
  folder and shared folder (your normal `data` folder is not used);
- starts a stand-in customer MES on this PC that receives what CNCBridge sends, with an
  inbox page you can open in a browser;
- opens CNCBridge already set up for the demo. Press **Start monitoring** there.

The Demo control window then drives the show:

| Button | What it demonstrates |
|---|---|
| Start machine / Drop one board now | board files arriving, being converted, delivered and backed up |
| Send a duplicate | the same content is not sent twice |
| Drop a broken file | an unusable file is put aside and work continues |
| Write a file slowly | a file is not touched until the machine has finished it |
| Simulate outage / End outage | files wait in the queue and are sent in order afterwards |
| Refuse next file | one refused file is put aside, the rest continue |
| Copy all test files / Copy one | eight fixed, numbered test files, one situation each |
| Open test script | the step-by-step sheet: do this, see that |
| Reset demo | empties the demo folder (close CNCBridge first) |

The test data are invented pick-and-place board logs. Each has a header block (`B5`
program, `B6` board serial), a summary block at `L1:M8`, and a parts table from `A12`
whose length changes per board, so the demo uses all three kinds of range.

The fixed test files and the test script are also in `demo	est_files` and
`demo\TEST_SCRIPT.md`. `tests\test_demo.py` checks that every test file ends up where the
script says.

## Admin account and the operator view

On first start, CNCBridge asks you to create the single **Admin** password before setup can be opened.
Use at least 12 characters. Existing setup password hashes are retained during the settings migration;
if an existing password is shorter than 12 characters, it is accepted once and CNCBridge requires it to
be changed before setup can be unlocked. Admin sign-in is case-insensitive for the username.

Choose **Settings > Lock setup** or **File > Log out** when setup should be hidden. Locked, the window shows only:

- **Start monitoring**, **Stop** and **Process now**, and the counters;
- **History**, and the activity log under the View menu;
- an **Operator** tab with the typed values (for example Operator and Work order) and **Apply**,
  which saves them. They are used from the next file, without stopping.

Setup, Delivery and Pick ranges are hidden, and Save settings, Options, Open CSV file and
Simulation (and the other setup entries in the menus and their shortcuts) are disabled. **Unlock setup...** asks for the password. A restart starts locked.
In **Settings > Operators & access...**, **Change Admin password...** verifies the current password and ends the
current Admin session after saving. The password cannot be removed. Sessions end after 8 hours; an optional
**Log Admin out after idle (minutes)** setting can shorten that period (0 disables the idle limit). Repeated
failed sign-ins use an increasing delay, up to 30 seconds.

The password is kept as a salted hash, never as text. It is not part of an exported settings file,
and importing a file or restoring an earlier version never changes it. This keeps settings from being
changed by accident. On its own it does not stop someone who can open this PC's files from editing
`config.json`: for that, switch on **Protect the settings** (next section).

## Protecting the settings

By default every Windows user of the PC can open the data folder, so anybody can edit `config.json` (and
drop the setup password) or read the saved passwords: they are encrypted for the PC, not for a person.
**Settings > Operators & access > Protection** locks that down. It needs administrator rights, so open
CNCBridge with **File > Restart as administrator** first (Windows asks for permission).

Tick **Only administrators can change the settings or read the saved passwords** and press Save settings.
From then on:

- `config.json` and the `backups` folder can be **read** by everybody but **changed** only by
  administrators;
- the passwords and tokens (API token, FTP password, mail password) are kept apart in `secrets.json`,
  which only administrators, the account that saved, and the accounts listed in the box can read;
- the typed values (Operator, Work order...) are kept in `operator.json`, which stays open, so an
  operator can still change them and confirm a shift change without administrator rights;
- an operator can still start and stop, and press Process now; monitoring then starts with the saved
  settings. Anything that needs a change of settings (Save settings, setting or removing the password)
  offers **Restart as administrator** instead, and the title bar says *(administrator)* while it is on.

**Accounts that may read the passwords.** Whoever runs the processing needs the saved passwords. If
monitoring runs in the background task of an administrator account, nothing else is needed. If an operator
account runs the window and processes files itself, list that account (one per line, as `DOMAIN\name` or
`name`); that account can then read the passwords, which is the price of processing as that account.

The status line in the box says whether the files are protected, or switched on but not yet applied (save as
administrator to apply it). The set-up program locks the files again after an update. Turn the tick off and
save as administrator to give the files back their ordinary permissions.

What this does not do: an administrator can always change everything, and the settings change log
(`settings_changes.jsonl`) stays writable for operators, because their typed-value changes are recorded in it;
the Windows Event Log keeps its own copy of each change that operators cannot edit. A mapped network drive
letter is not visible to a program started as administrator; use `\\server\share` paths for the folders.

## Settings history

**View > Settings history...** lists every change to the settings: when, which Windows user, how
(saved, imported, restored, password, typed values) and what changed, with the old and the new
value. Passwords, tokens and header values are never written, only that they changed; web addresses
are written without their query part. Select a line for the full list.

The log is `settings_changes.jsonl` in the data folder (the newest 5,000 entries are kept). Each
entry holds a fingerprint of the settings file as it was saved. If `config.json` no longer matches
the last entry, it was edited outside CNCBridge: that is added to the log as an entry in red, shown
in the status line, and sent through the alert channels.

## Shift-change confirmation

In **Settings > Operators & access...**, enter the times at which shifts start, for example
`06:00, 14:00, 22:00`. At each of them, a window asks the operator to confirm or change the typed
values (Operator, Work order), so a value from the last shift is not written into the next shift's
files. Files keep being processed while the question is open. **Later** closes it and leaves an
amber banner under the counters; clicking the banner asks again. **Confirm** saves any changed
values, and they apply from the next file.

The last confirmation is remembered across restarts: a shift change that nobody answered is asked
about again when the program is opened, and one that was answered is not. Setting the times for
the first time does not ask about a shift that began earlier. Leave the field empty to switch it
off (the default).

## Appearance

**Settings > Appearance...** controls how the window looks. Choose, press **Save settings**, then
**Restart now**: the window saves, closes and opens again by itself with the new look; if monitoring was
running it starts again at once. Appearance only changes how the window looks: processing, output files and
delivery are the same.

- **Theme.** **Modern** (default) has flat controls, light industrial colours and the bundled Roboto
  fonts, a real semibold font for titles, counters and buttons, large counters, clear tick boxes and radio
  buttons, a one-line page heading on each tab, and a scrollbar only when a tab is taller than the window.
  **Legacy** is the look of version 1.0: native Windows controls, Segoe UI and Consolas, compact spacing,
  bare counters and no page headings, in the same 1180 x 740 window, with everything added since
  version 1.0 drawn in the same style.
  **Dashboard** puts the navigation in a dark **sidebar** instead of tabs, adds a **Monitor** page, and draws
  the boxes as white **cards** on a calm grey page (see *The Dashboard theme* below).
- **Colours.** **Light**, **Dark** or **Follow Windows** (the app mode in the Windows settings). Modern has a
  designed dark palette. Legacy's dark mode **inverts its own colours** (light grey becomes near-black, the
  blue accent becomes amber), because the native Windows controls cannot take a normal dark palette; it
  draws the same classic controls, recoloured. The title bars follow.
- **Font.** The theme's own font, or one of the clear interface fonts installed on the PC (Segoe UI, Roboto,
  Arial, Calibri, Tahoma, Verdana, Bahnschrift and similar). Cell ranges and the preview stay monospaced.
- **Text size.** 90%, 100%, 115% or 130%: only the text grows; table rows and the window width follow it.
- **Window scale.** 90% to 150%: everything grows, including spacing, buttons and the menu bar. For large or
  touch screens.

- **Language.** The window's text in a language that has a file in `cncbridge\locales`. English is built in
  and is what you get for a missing translation; see *Translating* below.

The menu bar is drawn by the program, so it is larger than the Windows default (11 pt in Modern) and
grows with Text size and Window scale. **Alt** plus the underlined letter, or **F10**, opens a menu from the
keyboard. The drop-downs are drawn by the program too: with a menu open, Left and Right move to the
next menu, the pointer on another title switches to it, and nothing else in the window is frozen.

The choices are the `theme`, `color_mode` (`light`, `dark`, `system`), `font_family`, `font_size` and
`ui_scale` settings in `config.json`, and are part of Export/Import settings. An unknown or invalid value falls
back to the default.

### The Dashboard theme

The sidebar lists the pages (Monitor, Setup, Ranges & preview, Delivery, History, and the operator's own
page while the setup is locked) with Settings, Help and the Activity log below them. The page that is
open is marked, and a page that is hidden by the lock is not listed. Below the sidebar a card shows the
station's name, whether it is monitoring, and a Start monitoring / Stop button that works from every page.
The tab strip is hidden: the sidebar does its job (View > Monitor, Ctrl+0 to Ctrl+4, the checklist's buttons
and every other way of opening a page work as before). The menu bar is unchanged.

Instead of a toolbar, a white **header** at the top shows the open page's title, what the page is for, and
that page's own buttons: on the Monitor a tinted pill that says what the program is doing (green while
monitoring, amber while monitoring with a problem open, grey when stopped), Process now, and Start
monitoring or Stop, whichever applies; on Setup and Delivery, Save settings, with "Unsaved changes" beside
it while the form differs from the saved settings. The boxes are cards with their title inside, without
numbers.

While a problem is open an amber **banner** under the header says it in plain words: what is wrong,
whether anything is lost ("3 file(s) are safe in the send queue..."), what to do, and, small, the
program's own message and since when. Its button leads to where to look: the History for a destination
that is not answering, Setup for incomplete settings, Delivery for the heartbeat, otherwise the
Activity log.

The controls follow the design as well: the destinations are turned on and off with **switches**, the
output format is one **segmented** row (JSON | CSV | TXT | PDF), the API link's rarely needed settings
(send as, extra headers) sit behind **More**, which opens by itself when something is set in it, and each
folder field says under it whether the folder is there (for the machine folder: how many files wait in
it; a network folder is left to its Test button, since it can take seconds to answer). On the Monitor,
results and destinations are coloured **chips**; double-click a line to see its output.

The **Monitor** page is where the counters and the health line now live, so the other pages are free of
them: the five counters (each says what it counts), the last file, the age of the send queue and the
back-up drive, the latest files with where each went (Shared folder ✓, API link waiting...), one line per
destination (working, waiting to be sent, not answering since 10:08, or off) and the typed values with a
Change button. A copy that has run before opens on the Monitor; a new one opens on Setup with the guide.

The fonts are IBM Plex Sans and Plex Mono, bundled with the program (see *Bundled fonts*); if they cannot
be registered, Segoe UI and Cascadia Mono or Consolas. It has a designed dark palette too, and its colours were chosen so that text on
the cards and on the page reaches the 4.5 : 1 contrast that is readable at a glance (a test checks it).

For developers, a theme asks for these things in its `Layout`: `sidebar=True` (a sidebar, the tab strip
hidden), `monitor_page=True` (the Monitor page and its View menu entry), `chips=True` (switches, segmented
choice, More, folder checks and chips, see `ui/widgets.py`) and `cards=True` (the plain widgets
inside a box take the card's colour and each box gets a card edge, see `ui/cards.py`), `page_headers=True`
(the header bar and the plain-words banner instead of the toolbar and the red alert line, see
`ui/pageheader.py` and `messages.py`), and names the five `SIDEBAR_*` colours. The other
themes leave all three off and look exactly as before.

Adding a theme (for developers): in `cncbridge/theme.py` write a `Theme` with a value for every name in
`COLOUR_NAMES`, ten range colours, candidate fonts, a `Layout` (sizes and spacing) and a function that styles
the ttk widgets, then add it to `THEMES`. Give it `dark_colours` (and `dark_range_colours`) to offer a dark
mode; without them it stays light. It appears in the Appearance page on its own.

## Translating

Every text the window shows goes through `tr("English text")` (`cncbridge/i18n.py`), so the English text is the key
and a missing translation simply shows English. To add a language:

1. `python tools/extract_strings.py` writes `cncbridge/locales/template.json`, a list of every English text
   (window, menus, tooltips, messages, Help topics and shortcut descriptions).
2. Copy it to `cncbridge/locales/<code>.json`, for example `de.json`, set `"language"` to the language's own name
   (`"Deutsch"`) and fill in `"strings"`. An empty value means *not translated yet*. Keep every `{placeholder}`
   of the English text (`{count} file(s) queued` needs `{count}`).
3. The language appears in **Settings > Appearance... > Language**; choose it, Save, Restart now.

Padding spaces and the symbols in front of buttons are not part of the texts. **Not translated, on purpose:**
the log, the output files, what is sent to a receiving system (API, webhook, FTP) and the messages the processing
engine produces, such as the reasons listed by "Please complete the settings", because machines and other systems
read those. The option lists of a few drop-downs (text size, window scale, API body) are also English for now.
`python tools/extract_strings.py --check` tells whether the template is up to date; the tests fail when it is not,
or when new window text is not wrapped in `tr()`.

## Automatic clean-up

Off by default, so nothing is ever deleted unless you choose it. Under *Monitoring & file
options* in **Settings > Processing...**:

- **Delete back-up files after N days** removes back-up day folders (judged by the date in the
  folder name) and files in `back-up\_failed` and `_duplicates` older than N days.
- **Delete history after N days** removes History rows older than N days.

It runs once an hour while monitoring. It only deletes inside the back-up folder and the
history; files waiting to be sent and refused files are never touched.

## Good to know

- The API token is stored encrypted with Windows DPAPI, bound to this PC. A `config.json`
  copied to another PC keeps every setting except the token. Any Windows user of this PC
  can run the program with it.
- Closing the window stops monitoring. Files that arrive meanwhile wait in the machine
  folder and are processed at the next start.
- Not included: picking tables by title, column filters, automatic deletion of old back-ups.
- The rarely changed settings `stable_seconds` (default 2) and `api_timeout` (default 20)
  are only in `config.json` in the data folder.

## Signing the programs and Windows versions

**Signing.** An unsigned program can make Windows (SmartScreen) or antivirus software warn about it.
`build_exe.bat` signs both programs when it finds a code-signing certificate. Buy one from a certificate
authority (a certificate that is not self-signed, ideally with extended validation, is what removes the
warnings), save it as a `.pfx` file, and set before building:

```
set CNCBRIDGE_SIGN_CERT=C:\keys\company.pfx
set CNCBRIDGE_SIGN_PASSWORD=the-password-of-the-pfx
build_exe.bat
```

`CNCBRIDGE_SIGN_TIMESTAMP` is the timestamp server (default `http://timestamp.digicert.com`; `none` signs
without one). A timestamp keeps the signature valid after the certificate itself expires, so leave it on.
Without `CNCBRIDGE_SIGN_CERT` the programs are built unsigned and the script says so. The build checks that
the program is still complete after signing. The password sits in an environment variable, so do this on
the PC that builds the programs, not on the machine PCs.

Tested with a self-signed certificate: the signed program starts as a window and in the background, and
signing it again replaces the signature. A real certificate has not been tried.

**Windows versions.** The programs are built with Python 3.11, which supports Windows 8.1 and later, in the
64-bit edition. Windows 10 and 11 are the safe choice; Windows 7 is not supported. They have only been run
on Windows 11. Before relying on an older Windows, copy the two exe files to such a PC and run the
demo (`CNCBridgeDemo.exe`) there.

## Making a release

`release.bat` builds the programs and the set-up and writes `release\CNCBridge-<version>.zip` (the
set-up, the two programs and this README) and `release\CNCBridge-<version>.sha256` (checksums, so the receiving
PC can check the files arrived intact). Raise `VERSION` in `cncbridge\config.py` before each
release; an existing zip with the same version is never overwritten. The exe files are not
kept in git; publish the zip instead, for example on GitHub Releases with the command that
`release.bat` prints at the end.

## For developers

Python 3.11 (the version it is built and tested with), standard library only.

```
CNCBridge.pyw            double-click to run from source
cncbridge/engine.py      the Engine: the loop that reads, renders, queues, delivers and backs up
cncbridge/csvread.py     read a machine file, cell ranges
cncbridge/output.py      render JSON / CSV / TXT, check the settings (validate)
cncbridge/ledger.py      the history database (one row per file, one per file and destination)
cncbridge/delivery.py    shared folder, API link, FTP server, and their tests
cncbridge/housekeeping.py  queues, back-up drive, health, second chances, clean-up
cncbridge/config.py      settings, data folder, token encryption, setup password
cncbridge/alerts.py      Windows notification, webhook and email alerts
cncbridge/heartbeat.py   the regular status message; cncbridge/eventlog.py the Windows Event Log
cncbridge/audit.py       the settings change log
cncbridge/diagnostics.py the zip for support
cncbridge/permissions.py who may change the settings (icacls), asking Windows for administrator rights
cncbridge/firstrun.py    the marker that the first-start guide has done its job
cncbridge/background.py  background task, single-copy lock, connected window
cncbridge/shifts.py      shift-change times and the last confirmation
cncbridge/i18n.py           tr() and the language files; cncbridge/locales/ holds template.json
tools/extract_strings.py   writes the template of every English text
installer/                  the set-up program (core, window, command line); CNCBridgeSetup.pyw starts it
cncbridge/stations.py       the list of copies on this PC and a read-only look at each
cncbridge/app.py         the main window: puts the pieces below together, and run()
cncbridge/ui/            the pieces of the window, one mixin each (sidebar, monitor and cards for the Dashboard theme): layout (frame, toolbar, header), menus
                         (menu bar, shortcuts), setup_tab (Setup, Delivery), pick_tab, history_tab, options
                         (Options window, Appearance), access (password, operator view, shifts), windows
                         (log, Help), tooltips, form (settings <-> form, settings files), control (start/stop,
                         save, background, closing), status (live refresh), common, dialogs
cncbridge/help.py        the text of the Help window
cncbridge/grid.py        spreadsheet view with drag selection
cncbridge/theme.py       themes (Modern, Legacy): colours, fonts and widget styles
assets/fonts/            bundled Roboto/Roboto Mono fonts and OFL licenses
tests/test_engine.py     python -m unittest discover -s tests -v
tests/test_structure.py  the window's pieces fit together; every engine name is still reachable
tests/test_ledger.py     the history database and its migration
tests/test_audit.py      the settings change log and edits made outside CNCBridge
tests/test_diagnostics.py the zip for support: what it holds, and that no password is in it
tests/test_protection.py typed values and passwords in their own files; locking, with real permissions
tests/test_checklist.py  problems grouped by part of the setup, and the first-start marker
tests/test_heartbeat.py  the heartbeat; tests/test_eventlog.py the Event Log handler
tests/test_config_migrations.py  the settings file version
tests/test_installer.py  install, update and uninstall into temporary folders and a test registry key
tests/test_ftp.py        FTP / FTPS delivery against a small FTP server written for the tests
tests/test_theme.py      every theme is complete; unknown names fall back
tests/test_window.py     drives the real window: set CNCBRIDGE_WINDOW_TESTS=1 first
demo/                    test data, stand-in MES, Demo control window (CNCBridgeDemo.pyw starts it)
build_setup.bat          builds CNCBridgeSetup.exe from dist\CNCBridge.exe
build_exe.bat            builds CNCBridge.exe, and CNCBridgeDemo.exe where demo\ exists (options: app, demo, clean)
sign_exe.ps1             signs the programs (used by build_exe.bat)
release.bat, release.py  build, then pack a release zip with checksums
```
