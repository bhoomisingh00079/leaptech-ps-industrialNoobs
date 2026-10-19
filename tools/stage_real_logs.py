"""Stage the supplied data on first launch of the real-logs profile."""
import shutil
from pathlib import Path

from prepare_secom import prepare


def stage(root):
    logs = Path(root) / "real_machine_logs"
    incoming = logs / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    if any(incoming.glob("*.csv")):
        print("Machine folder already contains CSV files; leaving them unchanged.")
        return False
    if (logs / "linebridge_ledger.db").exists() or any((logs / "backup").rglob("*.csv")):
        print("Existing processing history found; not restaging archived records.")
        return False
    source = logs / "source"
    prepare(source / "secom.data", source / "secom_labels.data", incoming)
    shutil.copy2(source / "experiment_13.csv", incoming / "experiment_13.csv")
    print("Staged experiment_13.csv and 16 SECOM batches for processing.")
    return True


if __name__ == "__main__":
    stage(Path(__file__).resolve().parents[1])
