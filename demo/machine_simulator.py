"""
Pick-and-place machine simulator.

Writes a realistic multi-table CSV log into a folder every few seconds, the way a real
SMT / CNC assembly machine does after each board. Writing is done in chunks with a pause,
like real equipment, so you can see LineBridge wait for the file to be complete.

    python demo/machine_simulator.py --out demo/workspace/machine_out --every 6
"""
import argparse
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

PARTS = [("R101", "RC0402-10K"), ("R102", "RC0402-4K7"), ("C201", "CC0603-100N"), ("C202", "CC0603-1U"),
         ("C203", "CC0805-10U"), ("U1", "STM32F405RG"), ("U2", "TPS62160"), ("U3", "MCP2551"),
         ("D1", "LED0603-GRN"), ("Q1", "BSS138"), ("L1", "IND-4R7"), ("J1", "USB-C-16P")]
NOZZLES = ["N1", "N2", "N3", "N4"]
ERRORS = {"E101": "Pick-up failure", "E205": "Vision misalignment", "E310": "Component tombstone risk"}


def make_log(board_no, line="L1", machine="M8", seed=None, start=None):
    rnd = random.Random(seed)
    start = start or datetime.now()
    t = start
    placements, errors, feeders = [], [], {}
    for i, (ref, part) in enumerate(PARTS):
        t += timedelta(milliseconds=rnd.randint(250, 900))
        feeder = f"F{10 + i:02d}"
        nozzle = NOZZLES[i % 4]
        ok = rnd.random() > 0.08
        result = "OK" if ok else rnd.choice(["PICKUP_ERR", "VISION_NG"])
        placements.append([t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], ref, part, feeder, nozzle,
                           f"{rnd.uniform(5, 95):.2f}", f"{rnd.uniform(5, 75):.2f}", rnd.choice(["0", "90", "180", "270"]), result])
        f = feeders.setdefault(feeder, [feeder, part, rnd.randint(200, 4800), 0, 0])
        f[3] += 1
        if not ok:
            f[4] += 1
            code = "E101" if result == "PICKUP_ERR" else "E205"
            errors.append([placements[-1][0], code, ref, ERRORS[code]])
    end = t + timedelta(seconds=rnd.uniform(2, 5))
    noz = {n: [n, 0, 0] for n in NOZZLES}
    for p in placements:
        noz[p[4]][1] += 1
        noz[p[4]][2] += p[8] != "OK"

    out = [["[HEADER]"], ["Key", "Value"],
           ["Machine", f"PNP-{machine}"], ["Line", line], ["Program", "ECU-MAIN-REV3"],
           ["Board_Serial", f"B{240000 + board_no}"], ["Start_Time", start.strftime("%Y-%m-%d %H:%M:%S")],
           ["End_Time", end.strftime("%Y-%m-%d %H:%M:%S")], ["Cycle_Time_s", f"{(end - start).total_seconds():.1f}"],
           [], [f"[{line}x{machine}]"],
           ["Timestamp", "Ref", "Part_No", "Feeder", "Nozzle", "X_mm", "Y_mm", "Rot_deg", "Result"], *placements,
           [], ["[FEEDER_STATUS]"], ["Feeder", "Part_No", "Remaining", "Picks", "Misses"], *feeders.values(),
           [], ["[NOZZLE_STATS]"], ["Nozzle", "Picks", "Errors", "Error_Rate_pct"],
           *[[n, p, e, f"{100 * e / p:.1f}" if p else "0.0"] for n, p, e in noz.values()],
           [], ["[ERRORS]"], ["Timestamp", "Code", "Ref", "Description"], *errors]
    width = 9
    return "\n".join(",".join((list(map(str, r)) + [""] * width)[:width]) if r else "," * (width - 1) for r in out) + "\n"


def run(out_dir, every=6.0, count=0, duplicate_every=0, line="L1", machine="M8"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n, last = 0, None
    while not count or n < count:
        n += 1
        if duplicate_every and n % duplicate_every == 0 and last:
            text, name = last, f"{line}{machine}_board_{n:05d}_RESENT.csv"   # machine re-sends an old log
        else:
            text, name = make_log(n, line, machine), f"{line}{machine}_board_{n:05d}.csv"
            last = text
        p = out / name
        half = len(text) // 2
        with open(p, "w", newline="") as f:     # write in two chunks, like a real machine
            f.write(text[:half]); f.flush()
            time.sleep(0.6)
            f.write(text[half:])
        print(f"[machine] wrote {name}", flush=True)
        time.sleep(every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo/workspace/machine_out")
    ap.add_argument("--every", type=float, default=6)
    ap.add_argument("--count", type=int, default=0, help="0 = forever")
    ap.add_argument("--duplicate-every", type=int, default=0)
    ap.add_argument("--sample", help="write one sample file to this path and exit")
    a = ap.parse_args()
    if a.sample:
        Path(a.sample).write_text(make_log(1, seed=7, start=datetime(2026, 10, 9, 10, 0, 0)))
    else:
        run(a.out, a.every, a.count, a.duplicate_every)
