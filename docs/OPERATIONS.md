# Operations

<!-- M3-ysup-receiver -->
## YSUP-Link receiver

The optional receiver listens only while monitoring is running and is off by default. It binds to `127.0.0.1`
unless configured otherwise. To accept machine traffic from another host, configure `0.0.0.0` (or a specific
interface), set Basic Auth credentials or an API token, and allow the configured TCP port through the Windows
firewall only for the required machine/network. The default port is `3001`.

If the port is already in use, CNCBridge reports a receiver problem in its normal log/alert path and continues
monitoring machine files. Stop the other listener or choose a free port, save settings, and restart monitoring.
The exchange ledger is `ysup.db` in the CNCBridge data folder and follows `keep_history_days`; `0` keeps all rows.
<!-- /M3-ysup-receiver -->
