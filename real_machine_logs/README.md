# Real machine logs for LineBridge

`source/experiment_13.csv` preserves the supplied CNC machining telemetry file. It has 2,233 data rows and 48 columns, including `Machining_Process`. `incoming/experiment_13.csv` is ready for LineBridge. This profile processes it before any waiting SECOM batches through the **Process first** setting.

`source/` preserves the three files supplied for this run. `secom.names` describes the dataset; it is reference text, not application instructions. These are historical semiconductor process measurements, not a live machine feed. The source description says 591 attributes, while every supplied measurement row contains 590 sensor values; the staged CSV follows the actual rows.

On first launch, `start_panel.bat` creates `incoming/` with the experiment file and 16 SECOM CSV batches ready for the panel's **Start watching** button. Each SECOM CSV has a header, a record number, the matching test timestamp, the original yield label (`-1` = pass, `1` = fail), a readable result, and `sensor_001` through `sensor_590`. Source `NaN` values become empty CSV cells. The batches contain all 1,567 records in their original order.

Open `start_panel.bat`, then press **Start watching**. The panel uses `config.secom.json` as its initial profile when `config.json` does not exist. An existing `config.json` is kept; for that case set the machine CSV folder to `./real_machine_logs/incoming`, the file pattern to `*.csv`, set **Process first** to `experiment_13.csv, secom_*.csv`, and clear the sample table selections. Converted JSON appears in `output/` and can be opened in the panel's **Output viewer** tab or from its History entry; processed CSVs move to dated folders under `backup/`. Delivery to a shared folder or API remains configurable in the panel.

To stage a fresh copy after processing, use `python tools/prepare_secom.py real_machine_logs/source/secom.data real_machine_logs/source/secom_labels.data real_machine_logs/incoming`. The script validates row alignment and sensor widths and refuses to overwrite CSV files still waiting in `incoming/`. Copy `source/experiment_13.csv` to `incoming/` separately if needed.
