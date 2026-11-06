# Architecture

CNCBridge has one process holding a data-folder lock. That process owns the file watcher, delivery retry queue,
and optional YSUP-Link listener. The window and `--headless` background process use the same Engine; a connected
window does not start a second listener.

<!-- M3-ysup-receiver -->
## YSUP-Link listener

When `ysup_enabled` is on, `Engine.run_forever` starts `cncbridge.ysup.server.YsupServer` on the configured
address and port. It accepts JSON POST requests at `/` and `/ysup/api`, applies authentication and request checks,
and dispatches response formatting to `cncbridge.ysup.replies`. Each exchange is stored in `ysup.db` by
`cncbridge.ysup.records`; history cleanup follows `keep_history_days`. Stopping monitoring closes the listener. A
bind failure is reported through the standard alert and log path without stopping file processing.
<!-- /M3-ysup-receiver -->
