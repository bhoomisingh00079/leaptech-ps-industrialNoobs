# Configuration reference

Settings are stored in `config.json` in CNCBridge's data folder. The settings file is versioned and migrated
automatically when an older configuration is opened. Version 5 preserves the existing `setup_password` hash
and adds the following Admin session setting:

| Key | Default | Meaning |
|---|---:|---|
| `admin_idle_minutes` | `0` | Log the Admin out after this many idle minutes. `0` disables the idle limit. The Options control ranges from 0 to 480 minutes. |
| `setup_password` | empty | Salted PBKDF2-SHA256 hash for the single Admin account. Do not edit this value manually. |

The Admin session always expires after 8 hours, whether or not an idle limit is configured. The one account's
username is `Admin`; matching ignores case. New passwords must contain at least 12 characters. Existing hashes
are preserved by migration. If an existing password is shorter than 12 characters, CNCBridge accepts it for
one sign-in and requires a password change before setup is unlocked.

Use **Settings > Operators & access...** to change the password or set the idle limit. Passwords are never
included in settings exports, audit details, or log messages. Changing a password ends the current Admin
session. There is no option to remove the Admin password.
