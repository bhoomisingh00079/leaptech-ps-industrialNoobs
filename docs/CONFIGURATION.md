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

<!-- M2-materials -->
## Materials database settings (settings version 6)

| Key | Default | Meaning |
|---|---|---|
| `ysup_database` | `ysup_materials.csv` | Imported material CSV. Relative paths resolve under the CNCBridge data folder; absolute paths are also accepted. |
| `ysup_fields` | `{"part_number": true, "reel_id": true, "lot_id": false}` | Fields a YSUP lookup compares. Partial objects merge over these defaults. |
| `ysup_extra_fields` | `[]` | Optional extra customer column names to compare when a request includes them. Blank names are removed. |
| `ysup_import_folder` | empty | Optional folder for automatic CSV imports. Successful files move to `imported`; rejected files move to `rejected`. |

The database uses the CSV itself so CNCBridge and YSUP-Link can share the same file. Imported columns are retained;
the standard template headers are `Part Number, Description, Reel ID, LOT ID, Quantity`. The adjacent
`ysup_materials.refused.json` stores skipped rows and last-import details against the SHA-256 of the stored CSV.
If another program replaces the CSV, CNCBridge ignores sidecar details whose hash no longer matches.

Automatic folder imports run on each processing scan, and every few seconds in an open CNCBridge window while
monitoring is stopped. Invalid imports raise an alert and do not replace the previous database. No additional
Python package is required.
<!-- /M2-materials -->
