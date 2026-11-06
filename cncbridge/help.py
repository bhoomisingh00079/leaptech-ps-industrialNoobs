"""
The text of the Help window, one entry per topic: (key, title, text).

A small markup keeps the text easy to edit: a line starting with "## " is a sub-heading, "- " is a bullet,
four spaces start a line of code, a blank line separates paragraphs. A backslash at the end of a line in this
file only joins source lines.
"""

SHORTCUTS = (
    ("Ctrl+O", "Open a machine CSV file in the range picker"),
    ("Ctrl+S", "Save settings"),
    ("F5", "Start monitoring"),
    ("Shift+F5", "Stop monitoring"),
    ("F6", "Process the waiting files once"),
    ("Ctrl+,", "Options"),
    ("Ctrl+L", "Activity log"),
    ("Ctrl+1 ... Ctrl+4", "Go to Setup, Delivery, Pick ranges & preview, History"),
    ("F1", "Help"),
    ("Alt+F, M, S, T, V, H", "Open the File, Monitoring, Settings, Tools, View or Help menu"),
    ("F10", "Open the first menu"),
    ("Arrow keys, Enter, Esc", "In an open menu: move (Left and Right change menu), choose, close"),
)

TOPICS = (
    ("start", "Getting started", """\
CNCBridge takes the CSV file your machine writes for each board, cuts out the cell ranges you choose, adds the \
details you type, and delivers the result to a shared folder, an API link and/or an FTP server. The machine file is then moved \
to a back-up folder.

## First set-up, in five steps
On a new set-up the Setup tab starts with a short guide, "Getting started": four steps with a tick for each one that is done, and a button that takes you to the next. It goes away after the first start; View > Setup checklist shows it again.

- Setup tab, box 1: choose the machine folder and a back-up folder. They must be different folders.
- Setup tab, box 2: choose JSON, CSV, TXT or PDF.
- Setup tab, box 3: say what to extract. Type a range such as L1:M8, or press Pick... and drag over a real file.
- Delivery tab: turn on the shared folder, the API link, the FTP server, or several, and press the Test buttons.
- Press Save settings (Ctrl+S), then Start monitoring (F5).

## Day to day
Start monitoring and leave the window open. The counters at the top show what has been processed, what is waiting \
and what needs attention. History lists every file and where it went.

Everything that is set once and rarely changed (output switches, alerts, the setup password, the look of the \
window) is under Settings > Options, so the tabs only hold what you work with.
"""),

    ("works", "How CNCBridge works", """\
For every file the machine writes, CNCBridge does these steps in order:

- Waits until the machine has finished with the file: unchanged for 2 seconds and no longer held open. If your \
machine writes in bursts, set "File is complete when it contains" (Options > Processing) to the text the machine \
writes last.
- Reads it (comma, semicolon, tab or | separated; UTF-8, UTF-16 or a Windows code page).
- Cuts out the ranges you chose and adds the typed details, the file name, the time and the station name.
- Writes one output file in the chosen format and puts it in a send queue on this PC.
- Moves the machine file to the back-up folder, in a sub-folder for the day.
- Delivers the output to every destination that is turned on. If a destination cannot be reached, the file waits \
in the queue and is sent in order when it is back.

Nothing is lost if the PC restarts: settings, history and the queue are kept on disk in the data folder \
(File > Open folder > Data folder).
"""),

    ("ranges", "Choosing cell ranges", """\
Up to 10 ranges can be extracted. Each has a name for the output, a cell range, and a tick for "first row is header".

## Writing a range
    L1:M8      a block: from cell L1 to cell M8
    B6         one cell
    A12:I*     from A12 down to the first empty row
    @[ERRORS]  the table under the cell that says [ERRORS]
    @NOZZLE*   every table whose title fits: * stands for any text, ? for one character
    A12:I*[Ref, Result]  only these columns, in this order

L1 x M8 means the same as L1:M8. A12:I* is for tables whose length changes from board to board. @[ERRORS] finds \
the table wherever it is in the file, so it still works when the tables above it grow or shrink. A heading with * \
or ? gives one block for each table whose title fits, named after that title.

A column list in square brackets can follow any block with a header row, including a heading: \
@[ERRORS][Code, Description]. Column names are matched ignoring upper and lower case, by their name in the file \
or the new name from Rename columns. A name the file does not have is left out, with a warning.

## Using Pick...
Open a real machine file (Ctrl+O, or "Newest machine file"). The file appears as a grid with column letters and \
row numbers. Drag over the cells you want, choose a slot, give it a name and press Assign. The panel on the right \
shows exactly the file that will be produced.

- Tick "down to first empty row" to turn the block into one that grows with the table.
- Tick "Find by heading" before marking a cell to follow a table that moves: the range becomes @ plus the text of \
that cell.
- "Tables in this file" lists every table that has a title over its header row, such as [ERRORS]. Click one: it is \
marked in the grid and becomes @ plus its title. Press Assign (or double-click it) to put it in the chosen slot.

If a range finds nothing, that selection is empty and CNCBridge warns about it. A file where every selection is \
empty is set aside as failed.
"""),

    ("delivery", "Delivery, queue and retries", """\
## Destinations
- Shared folder: a local or network path such as \\\\MES-SERVER\\inbox. Test shared folder checks that it can be written to.
- API link: the address the files are POSTed to, with an optional token (sent as Authorization: Bearer ...), extra \
headers and a choice between the file as the request body or as a form upload. Test API link sends a small test \
file.

- FTP server: uploads each output file to an FTP server, optionally encrypted (FTPS). Enter the server, port, login and a folder on the server (missing folders are created). Files go up under a temporary name starting with a dot and are renamed when complete, so nobody reads half a file; a name that is already taken gets a number. Test FTP server uploads and removes a small test file.

## When something is not reachable
- Files wait in the queue and are sent in order once it is back. Machine files are still processed and backed up.
- If the API refuses one particular file (for example HTTP 400 or 422), that file is set aside in "rejected" in the \
data folder and the others continue. Fix the cause, then use Send again in History.
- A wrong token or link (401, 403, 404 or a redirect) is treated as a problem with the link: everything waits until \
the settings are corrected.
- A machine file with no data in any range, or that is not a readable CSV, goes to back-up\\_failed. The same \
content arriving again goes to back-up\\_duplicates and is not sent (switchable in Options > Processing).

## History
Select rows and use Send again (re-queue files the site refused) or Process again (put set-aside machine files back \
in the machine folder, for example after correcting a range).

View output (or a double-click on a row) shows the output file that was made: JSON and CSV as tables with a filter, TXT as it is, a PDF with a button that opens it. A copy of each output is kept in the back-up folder, in a folder "outputs" in each day folder, until the back-up clean-up deletes that day.
"""),

    ("alerts", "Alerts and background processing", """\
## Alerts (Settings > Alerts)
CNCBridge can tell someone when it needs attention instead of only writing to the log: a problem that lasts longer \
than the set time (default 10 minutes), and a file that was set aside. When a problem clears, a "working again" \
message follows.

- Windows notification on this PC: on by default, needs nothing.
- Webhook: a JSON POST. Teams and Slack incoming webhooks accept it.
- Email: mail server, port and the address to send to, plus a login if the server asks for one.

Use "Send test alert" (also Tools > Send test alert) to try every channel that is switched on. While a problem is \
open, a red banner under the counters shows it.

## Heartbeat (Settings > Heartbeat)
While monitoring runs, CNCBridge can send a small status message every few minutes to a web address (a JSON POST) and/or write it as <station>.status.json into a folder: who this is, whether all is well or what is wrong, the counters, the last file, the oldest waiting file and the free space on the back-up drive. When monitoring is stopped on purpose, one last message says "stopped". A monitoring tool or the MES that stops hearing from this copy without such a message knows that the PC or the program is down. Use "Send test heartbeat" (also Tools menu) to try it.

## Windows Event Log
Warnings and errors, and the main events (monitoring started or stopped, settings changed, an alert raised or cleared), are also written to the Windows Event Log (Application, source CNCBridge), where IT tools that already watch the PCs can see them. The same message is written at most once every ten minutes. The set-up program registers the source; a copy that was not installed still writes the events.

## Which files, in which order (Options > Processing)
The File pattern on the Setup tab says which files are machine files; several are separated by ; for example *.csv; *.json. Files are taken oldest first. "Process these files first" puts files whose names fit its patterns ahead of the others, in the order given, for example urgent_*.csv; rework_*.

A JSON machine file is shown and cut like a CSV: single values become name, value rows at the top, and each list of records becomes a table under a title such as [placements], so @[placements] finds it.

## Background processing (Options > Processing)
Tick "Process in the background" and Save settings. Processing then runs as a Windows background task for your \
user: it goes on after this window is closed and starts by itself when you log on. The window then opens in a \
connected mode, showing the counters and history and saving settings for the task to pick up. Start and Stop \
control the task.
"""),

    ("operators", "Operators, shifts and the setup password", """\
## Typed values
Setup tab, box 4: two name/value pairs, for example Operator and Work order. A changed value applies from the next \
file after Save settings; monitoring does not have to be stopped.

## Shift change (Settings > Operators & access)
Enter shift start times such as 06:00, 14:00, 22:00. At each, the operator is asked to confirm or change the typed \
values. Processing never pauses for it; a banner stays until it is confirmed.

## Admin account (Settings > Operators & access)
On first start, create the single Admin password (at least 12 characters) to unlock setup. Sign in through Settings > \
Unlock setup or File > Log out. The username is Admin, case-insensitive. Locked, operators can still start and stop \
processing, see the counters and History, and apply typed values. Change Admin password verifies the current \
password and ends the current session. Admin sessions end after 8 hours; an optional idle limit can shorten that \
time. Failed sign-ins are delayed increasingly, up to 30 seconds. Existing short setup passwords are accepted \
once and must be changed before setup unlocks. The salted password hash is retained across settings migration and \
is never written in plain text.

## Protecting the settings (Settings > Operators & access)
By default every Windows user of this PC can edit the settings file and read the saved passwords. "Only administrators can change the settings or read the saved passwords" locks them: operators can still start and stop, and change the typed values, but anything else needs administrator rights, and the window offers File > Restart as administrator. Switching it on or off needs an administrator session. If an operator account itself runs the processing, list it in the box so that it can read the saved passwords.

## Settings history (View > Settings history)
Every change to the settings is recorded: when, by which Windows user, and what changed (old and new value). Passwords and keys are never shown, only that they changed. If the settings file is edited outside CNCBridge, that is recorded too, shown in red, and announced as an alert.
"""),

    ("stations", "Several machines: Stations", """One copy of CNCBridge looks after one machine folder, with its own settings, history and queue. To run two lines on one PC, start a second copy with its own data folder (a shortcut to CNCBridge.exe --data C:\\CNCBridge\\line2).

View > Stations... shows every copy on this PC side by side: its name, whether it is open in a window, running in the background or stopped, the counters, the last file, the back-up drive's free space and any open problem. It only reads the other copies; it never changes them or takes over their work.

- Open starts CNCBridge on the selected copy's data folder (a copy that is already open in a window cannot be opened twice).
- Open data folder shows its files in Explorer.
- Add... lists a data folder that is not listed yet (the folder that contains config.json).
- Remove from list only takes it off the list; nothing is deleted, and it is listed again when it starts.
"""),

    ("themes", "Appearance: theme, colours, font and size", """\
Settings > Appearance... controls how the window looks. Everything here is applied when the window restarts: choose, \
press Save settings, then Restart now. The window saves, closes and opens again with the new look; if monitoring was \
running it starts again by itself. Appearance only changes how the window looks: processing and output are the same.

## Theme
- Modern (default): flat controls, light industrial colours, semibold emphasis, large counters.
- Legacy: the look of version 1.0: native Windows controls, Segoe UI and Consolas, compact spacing.
- Dashboard: a dark sidebar instead of tabs, a header with each page's own buttons, a Monitor page with the counters, the latest files and the state of each destination, problems said in plain words, and white cards on a calm grey, in the IBM Plex fonts.

## Colours
- Light, Dark, or Follow Windows (the app mode set in Windows settings).
- Modern and Dashboard have a designed dark palette. Legacy's dark mode inverts its own colours, because its native Windows \
controls cannot take a normal dark palette.

## Language
The window's text can be shown in any language that has a file in cncbridge\\locales; English is built in. Choose it \
under Language. Texts without a translation stay in English. The log, the output files and what is sent to other \
systems are never translated.

## Font, text size and window scale
- Font: the theme's own font, or one of the clear user-interface fonts installed on this PC.
- Text size (90% to 130%) enlarges only the text; table rows grow with it.
- Window scale (90% to 150%) enlarges everything: text, spacing, buttons and the menu bar. Use it on a large screen \
or for a touch screen.
"""),

    ("words", "Words used in CNCBridge", """\
## Files and folders
- Machine file: a CSV file the machine writes (a JSON file works too). Machine folder: where the machine writes them.
- Back-up folder: where a machine file is moved once it is handled, with a folder for each day.
- Output file: the JSON, CSV, TXT or PDF file CNCBridge makes from a machine file.
- Data folder: the settings, the history, the send queue, the refused files and the logs of this copy.

## Where output files go
- Destination: where an output file is sent. There are three: the shared folder, the API link and the FTP server.
- Send queue: output files that are waiting for a destination. They are sent in order when it can be reached again.

## The counters
- Done, Waiting to be read, Waiting to be sent, Could not be read and Refused: see Troubleshooting.
- Set aside: a file that CNCBridge moved out of the way instead of handling. A machine file that could not be read, or that is a duplicate, goes to the back-up folder. An output file that was refused goes to the data folder. Each can be tried again from History.

## What is typed
- Typed values: the two name and value pairs, for example Operator and Work order, that are written into every file. The engineer chooses the names; the operator changes the values.
- Cell range: part of a machine file, written as in a spreadsheet, for example L1:M8.
- Setup password: the password that locks the setup for operators. Protect the settings: lets only administrators change the settings.

## Watching from outside
- Alert: a message to a person when something needs attention. Heartbeat: a regular message that says the program is alive.
- Stations: the copies of CNCBridge on this PC, side by side.
"""),

    ("trouble", "Troubleshooting", """\
## The counters
- Done: machine files that were read and handled. Waiting to be read: files in the machine folder that have not been taken yet.
- Waiting to be sent: output files waiting for a destination. Could not be read: machine files that were set aside in the _failed folder inside the back-up folder.
- Refused: output files that the receiving site (the API link or the FTP server) refused. They are in the rejected folder of the data folder; History > Send again tries them once more.

## The line under the counters
- Last file: when the latest machine file was handled, and how long ago. A long time with a busy machine means something is stuck.
- Oldest in queue: how long the oldest output has waited to be delivered. Amber when it passes the alert time (Options > Alerts), red at three times that.
- Back-up drive: free space where the back-up folder is. Amber under 2 GB or 5%, red under 1 GB; an alert is sent when it stays low, because a full drive stops processing.
- "Machine folder not reachable" appears in red when the machine's folder cannot be opened.

## Common situations
- Nothing happens after Start: check Setup (folders, at least one range) and Delivery (a destination turned on). \
Save settings reports what is missing.
- "Waiting to be sent" keeps growing: the destination is unreachable or the token is wrong. Use the Test buttons on \
the Delivery tab, and check the Activity log (View menu).
- A file stays in the machine folder: the machine may still be writing it, or "File is complete when it contains" \
never appears in it.
- Settings were lost or changed: File > Restore an earlier version... brings back one of the last 20 saves.
- Asking support for help: Help > Export diagnostics... saves one zip with the log, the settings (without passwords), the history and the versions. Send that file; nobody has to ask for the pieces one by one.

## Where things are
- File > Open folder > Data folder: settings, history, send queue, rejected files and logs.
- View > Activity log: the technical messages. The same text is in logs\\cncbridge.log in the data folder.
"""),

    ("keys", "Keyboard shortcuts", ""),
)
