# Features

## Admin account and operator view

CNCBridge has one `Admin` account. On first launch, the application requires creation of an Admin password with
at least 12 characters before setup is available. The username comparison ignores case. For existing installs,
the version 5 settings migration retains the old setup-password hash. A password shorter than the new minimum can
be used once, then must be changed before setup unlocks.

An Admin can lock setup or log out, returning the application to its operator view. Operators can continue
monitoring controls, use History, and apply typed values. Admin sessions expire after 8 hours; the optional
`admin_idle_minutes` setting provides an earlier idle timeout. Failed sign-ins are delayed exponentially, up to
30 seconds. Changing the password requires the current password and terminates the session. The Admin password
cannot be removed.

Admin access events are recorded in the settings activity ledger and, on Windows, the Application Event Log:

| Event ID | Event |
|---:|---|
| 1200 | Admin login |
| 1201 | Failed Admin login or password-change authentication |
| 1202 | Admin logout or expired session |
| 1203 | Admin password created or changed |

Event and audit details never contain the password. See [Configuration reference](CONFIGURATION.md) for the
`admin_idle_minutes` setting and migration behavior.
