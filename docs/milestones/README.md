# Milestone status

| Milestone | Status | Branch |
|---|---|---|
| M1 — Admin login | Implemented; Windows GUI verification pending | `desktop-app` |
| M2 — Material database | Implemented; Windows GUI verification pending | `desktop-app` |
| M3 — YSUP-Link receiver | Implemented; Windows receiver/UI verification pending | `desktop-app` | <!-- M3-ysup-receiver -->

M1 adds the single Admin account, first-run password creation, password migration and change flow, lock/logout,
session expiry, login backoff, audit/Event Log events, operator-view access, and related documentation and tests.

M2 adds the removable Materials extension, CSV parsing and import reports, the shared database and refusal sidecar,
automatic folder imports, search/export UI, configuration migration, and related documentation and tests.

<!-- M3-ysup-receiver -->
M3 adds the optional YSUP-Link HTTP receiver, Basic/token authentication, safe request replies, exchange history,
retention cleanup, settings, and related documentation and tests. Setup and Material verification replies remain
safe placeholders for M4. <!-- M3-ysup-receiver -->
