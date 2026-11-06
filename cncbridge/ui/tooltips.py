"""Tooltips for the window's controls."""
from .. import help as help_text
from ..config import FORMATS, MAX_SELECTIONS
from ..tooltip import NotebookTabTooltip, Tooltip
from ..i18n import tr


class TooltipsMixin:
    # ================================================================ run control
    def _add_tooltips(self):
        """Attach concise help to the controls that benefit from extra explanation."""
        Tooltip(self.start_btn, tr("Start monitoring the machine folder for new machine files."))
        Tooltip(self.stop_btn, tr("Stop monitoring and processing."))
        for key, text in (("ok", tr("Machine files that were read and handled.")),
                          ("waiting", tr("Machine files in the machine folder that have not been taken yet.")),
                          ("queued", tr("Output files that are waiting to be sent to a destination.")),
                          ("failed", tr("Machine files that could not be read. They were set aside in the _failed "
                                        "folder inside the back-up folder.")),
                          ("rejected", tr("Output files that the receiving site (API link or FTP server) refused. "
                                          "They are in the rejected folder of the data folder."))):
            for widget in (self.count_boxes[key], self.counts[key], self.count_captions[key]):
                Tooltip(widget, text)
        Tooltip(self.once_btn, tr("Process the current files once without continuous monitoring."))
        Tooltip(self.save_btn, tr("Save all current settings to configuration."))
        Tooltip(self.sample_open_btn, tr("Choose a sample machine file to inspect and select cell ranges."))
        Tooltip(self.sample_newest_btn, tr("Open the newest file waiting in the machine folder or most recent back-up."))
        Tooltip(self.grid_view.canvas, tr("Drag across cells to select them. Arrow keys move; hold Shift to extend a range."))

        help_text = {
            "station_name": tr("Name used to identify this machine in output files."),
            "source_folder": tr("Folder where the machine places completed CSV files."),
            "source_folder_browse": tr("Choose the machine folder."),
            "file_pattern": tr("File names to watch; the default matches all CSV files. Several are separated by ; "
                            "for example *.csv; *.json"),
            "priority_patterns": tr("Optional. Files whose names fit these patterns are processed before the others, "
                                 "in the order given, for example urgent_*.csv; rework_*. Separate them with ;"),
            "backup_folder": tr("Processed machine files are moved here, grouped by date."),
            "backup_folder_browse": tr("Choose where processed machine files are archived."),
            "shared_enabled": tr("Turn on delivery to a local or network shared folder."),
            "shared_folder": tr("Optional destination folder, such as a network share."),
            "shared_folder_browse": tr("Choose the shared destination folder."),
            "test_shared": tr("Check that CNCBridge can write to the shared folder."),
            "api_enabled": tr("Turn on delivery to the receiving site's API."),
            "api_url": tr("Address that receives the output files."),
            "api_token": tr("Optional authentication token; CNCBridge stores it encrypted."),
            "api_body": tr("Choose whether the file is sent as the request body or as a form upload."),
            "api_headers": tr("Optional HTTP headers, one per line in Name: value format."),
            "test_api": tr("Send a small test file to check the API link."),
            "ftp_enabled": tr("Upload every output file to an FTP server, as well as to the other destinations."),
            "ftp_host": tr("The FTP server's name or address."),
            "ftp_port": tr("Usually 21, for both plain FTP and explicit FTPS."),
            "ftp_user": tr("Login name; leave empty for anonymous."),
            "ftp_password": tr("Stored encrypted, and never included when settings are exported."),
            "ftp_folder": tr("Folder on the server, for example /incoming/line1. Missing folders are created; "
                          "empty uses the login folder."),
            "ftp_tls": tr("FTPS: encrypt the login and the files (explicit TLS). Turn on when the server supports it."),
            "ftp_passive": tr("Passive mode, which works through most firewalls. Turn off only if the server needs it."),
            "test_ftp": tr("Upload a small test file and remove it again, to check the FTP settings."),
            "autostart": tr("Begin monitoring automatically when CNCBridge opens."),
            "skip_duplicates": tr("Ignore files whose content has already been processed."),
            "poll_seconds": tr("How often CNCBridge checks the machine folder for new files."),
            "encoding": tr("Choose a character encoding if CSV text looks incorrect; Auto is recommended."),
            "delimiter": tr("Choose the CSV column separator; Auto is recommended."),
            "alert_after_minutes": tr("A problem such as an unreachable destination must last this long before an "
                                   "alert is sent. Files set aside are announced at once."),
            "alert_notify": tr("Show a notification in the corner of this PC's screen."),
            "alert_webhook_enabled": tr("Post each alert as JSON to a link, such as a Teams or Slack incoming webhook."),
            "alert_webhook_url": tr("The address that receives the alert."),
            "alert_email_enabled": tr("Send each alert by email through your mail server."),
            "alert_email_server": tr("Name or address of the mail server, for example smtp.example.com."),
            "alert_email_port": tr("Usually 587 (with STARTTLS), 465 (encrypted) or 25."),
            "alert_email_starttls": tr("Encrypt the connection to the mail server. Leave on unless told otherwise."),
            "alert_email_user": tr("Leave empty if the mail server does not ask for a login."),
            "alert_email_password": tr("Stored encrypted, like the API token, and never exported."),
            "alert_email_from": tr("Sender address; the user name is used when this is empty."),
            "alert_email_to": tr("One or more addresses, separated by commas."),
            "test_alert": tr("Send a test message through every alert channel that is switched on."),
            "protect_settings": tr("Lock config.json and the saved passwords so that only administrators can change or "
                                   "read them. Operators can still start and stop, and change the typed values."),
            "password_accounts": tr("Windows accounts (DOMAIN\\name) that run the processing and need the saved "
                                    "passwords. Administrators and the saving account can always read them."),
            "restart_admin": tr("Close this window and open it again with administrator rights; Windows asks first."),
            "heartbeat_enabled": tr("Send a small status message at regular times while monitoring runs, and one "
                                 "last one when it stops on purpose."),
            "heartbeat_url": tr("The address that receives the status message as JSON (a POST), like an alert webhook."),
            "heartbeat_folder": tr("A folder to write <station>.status.json into, for an MES or tool that reads files."),
            "heartbeat_minutes": tr("How often the heartbeat is sent. A receiver can treat several missed ones as down."),
            "test_heartbeat": tr("Send one heartbeat marked as a test, to check the address or folder."),
            "keep_backup_days": tr("Back-up day folders and set-aside files older than this are deleted, checked "
                                "once an hour. 0 keeps everything. The send queue is never touched."),
            "keep_history_days": tr("History rows older than this are deleted. 0 keeps the whole history."),
            "run_in_background": tr("Applied with Save settings. Processing then runs as a Windows background task "
                                 "for this user; this window shows it and Start/Stop control it."),
            "pick_range": tr("Shows the cell range selected in the grid."),
            "json_numbers": tr("In JSON files, cells such as 12 or 40.53 become numbers. IDs with leading zeros, text and "
                            "numbers with a decimal comma stay text. Other formats are not affected."),
            "csv_flat": tr("CSV files become one plain table: a row per record, with the file, time, station, your "
                        "inputs and the selection name in front. Other formats are not affected."),
            "time_zone": tr("The processing time written in each file ends with this PC's time zone, such as +05:30. "
                         "Times copied from the machine file are not changed."),
            "rename_columns": tr("Change column names in every file, for example Ref=Reference. Applies to selections "
                              "that use a header row. Names are matched ignoring upper and lower case."),
            "complete_marker": tr("Optional. Text the machine writes last, such as End_Time. A file is taken only once "
                               "it contains this text (ignoring case), on top of the usual idle time. Leave empty "
                               "for machines that write a file in one go."),
            "pick_open": tr("Extend the selected range down to the first empty row."),
            "pick_heading": tr("Instead of fixed cells, find the block by the text in the top-left cell you marked. "
                            "It then still works when the table moves down the file."),
            "pick_name": tr("Name shown for this data in the output file."),
            "assign": tr("Save the selected range into the chosen extraction slot."),
            "clear_slot": tr("Remove the range and name from the chosen slot."),
        }
        for key, text in help_text.items():
            Tooltip(self.field_widgets[key], text)

        Tooltip(self.slot_box, tr("Choose which extraction slot receives the selected cells."))
        for fmt in FORMATS:
            Tooltip(self.field_widgets[f"output_format_{fmt}"], tr("Create the output as {format}.", format=fmt.upper()))
        for i in range(MAX_SELECTIONS):
            Tooltip(self.field_widgets[f"selection_name_{i}"], tr("Optional label used for this extracted data."))
            Tooltip(self.field_widgets[f"selection_range_{i}"], tr("Spreadsheet-style cells to include, e.g. A1:C8."))
            Tooltip(self.field_widgets[f"selection_header_{i}"], tr("Use the first selected row as column names."))
            Tooltip(self.field_widgets[f"selection_pick_{i}"], tr("Choose this range by selecting cells in a sample file."))

        self.tab_tooltip = NotebookTabTooltip(self.tabs, {
            0: tr("Machine folders, output format, data ranges and typed values."),
            1: tr("Shared folder, API link and FTP server, plus file-handling options."),
            2: tr("Select cells from a sample file and preview the resulting output."),
            3: tr("Review recent machine files and their delivery results."),
            4: tr("Technical event log for processing and delivery details."),
            5: tr("The typed values that go into every file."),
        })
        Tooltip(self.field_widgets["shift_times"], tr("Optional. At each of these times the operator is asked to confirm "
                                                   "or change the typed values. Files keep being processed meanwhile."))
        Tooltip(self.field_widgets["restart_now"], tr("Save, then close this window and open it again with the "
                                                   "chosen look. Monitoring resumes by itself."))
        Tooltip(self.field_widgets["change_admin_password"], tr("Change the single Admin account password."))
        Tooltip(self.field_widgets["admin_idle_minutes"], tr("Automatically return to the operator view after this many idle minutes; 0 disables idle logout."))
        Tooltip(self.field_widgets["operator_apply"], tr("Save the typed values; they apply from the next file."))
        Tooltip(self.slot_box, tr("Select which slot (range) you are editing."))
