# LineBridge Pitch Kit

Everything you need to present: a one-liner, a 3-minute script, a live demo flow, slide outline, and answers to likely judge questions.

---

## One-liner
**"LineBridge turns the CSV logs every assembly machine already writes into exactly what any factory's MES expects. It is delivered, backed up and audited, with zero changes to the machine."**

## The hook (pick one)
- "Every board on this line produces a data file. Most of those files end up nowhere, or get copied by hand at 3 a.m."
- "If a car's ECU fails in the field, the factory must prove which feeder and nozzle placed which resistor. That proof is sitting in a CSV on a machine PC."

---

## 3-minute script

**1. Context (30 s)**
"In electronics assembly, every CNC or pick-and-place machine logs each action to a CSV file, one file per board, all day. Factories run an MES to track production and traceability. But every MES make speaks its own protocol and wants its own format, and the machine's CSV has many tables mixed together."

**2. Problem (30 s)**
"So today, integrating one machine with one MES is either manual copying, a fragile script, or an expensive vendor project, and it has to be redone for every new customer. Newer standards like IPC-CFX help only if the machine vendor supports them. The installed base of older machines only writes files."

**3. Solution (40 s)**
"LineBridge runs on the machine PC. It watches the folder, picks up each CSV the moment it's complete, extracts only the tables you choose (up to 10, by name like L1×M8 or by cell range), adds two operator inputs like Operator and Work Order, and writes JSON, CSV or TXT. It then delivers to a shared folder, the customer's API, or both. Finally it moves the original to a dated back-up folder so the machine folder is always clean."

**4. Live demo (60 s)** (see flow below)

**5. Why it wins (20 s)**
"No machine changes. No install, because it's pure Python and works offline. It never loses a file thanks to the retry queue, and it never sends one twice thanks to fingerprinting. It proves every delivery with an audit trail. Onboarding a new customer is a settings change, not a project."

**Close (10 s)**
"If the machine can write a file, LineBridge can feed any MES. Thank you."

---

## Live demo flow (practise this)

Before going on stage: `python demo/run_demo.py --reset`, and arrange the two browser windows side by side (left: LineBridge `:8600`, right: Customer MES `:8080`).

| # | Do | Say |
|---|---|---|
| 1 | Point at the KPI row ticking up and the MES inbox filling | "A simulated pick-and-place machine is finishing a board every few seconds. Each log is converted and lands at the customer automatically." |
| 2 | **Preview & test** tab → drop `samples/pnp_L1M8_board_00001.csv` | "Here's one raw log. LineBridge found five tables and shows exactly where each sits, like A12:I24." |
| 3 | Click **Add** on FEEDER_STATUS; switch format to **TXT**, then **CSV** | "One click to select, and the output preview updates instantly. Same data, whatever format the MES wants." |
| 4 | On the MES page click **Simulate outage** | "Now the customer's server goes down, which happens all the time." |
| 5 | Show **Queued for delivery** rising and the warnings in **Live log** | "Nothing is lost. Files are created, backed up, and queued." |
| 6 | Click **Restore service** | "Service is back, and the queue drains by itself in the right order." |
| 7 | **History** tab, point at a `duplicate` row | "The machine re-sent an old log. LineBridge recognised the content fingerprint and blocked it. And here's the audit trail with a hash for every file, which answers the question 'did we send it?'" |

Backup plan if something fails live: the screenshots in `docs/screenshots/` show every step.

---

## Suggested slides (8)

1. **Title:** LineBridge, the last-mile adapter between machines and MES
2. **The line today:** machine → CSV → ??? → MES (manual copy, scripts, vendor projects)
3. **Why it matters:** traceability mandates (automotive, medical, aerospace), legacy machines, many customers × many MES
4. **Solution:** the 7-step pipeline diagram from the README
5. **Live demo**
6. **What's different:** zero-touch, zero-install, never lose / never duplicate, audit trail, configure-not-code
7. **Landscape:** CFX / Hermes / MES suites / Kepware & Node-RED / scripts, and where LineBridge fits (README §11)
8. **Roadmap and ask:** CFX publisher, machine profiles, field mapping, fleet dashboard; pilot on one line

---

## Impact framing

Use **your own** numbers if you have them from a real line; otherwise present these as an illustrative model and say so.

- *Manual effort:* if an operator spends **m** minutes per shift collecting and converting files, across **s** shifts and **n** machines, LineBridge saves `m × s × n` minutes per day.
- *Data completeness:* with the ledger, the share of boards with delivered machine data becomes measurable. The target is 100%.
- *Onboarding:* a new MES or customer becomes a configuration change (format, selections, link) instead of a development task.

---

## Judge Q&A

**Q: Isn't this just a CSV converter?**
A: Conversion is the easy 20%. The value is in the other 80% that breaks on real lines: detecting when the machine has finished writing, delivering to two channels with retry, never sending duplicates, archiving safely, and proving what was sent. Those are what make it trustworthy unattended.

**Q: Why not use IPC-CFX?**
A: We'd love to, and a CFX publisher is on our roadmap. But CFX has to be implemented by the machine vendor. LineBridge serves the large installed base that only writes files, and can make those machines *appear* as CFX sources later.

**Q: Every machine's CSV looks different. How do you handle that?**
A: Two ways of selecting data cover most layouts. Named sections work for logs with titled blocks, and Excel-style cell ranges work for anything else, including ranges that run to the last row. The preview shows exactly what's detected on a real file before going live. Vendor-specific parsers plug into one function.

**Q: What if the network or MES is down for hours?**
A: Outputs wait in an on-disk outbox per destination and are re-sent in order once it's back. Machine files are still archived, so the line never stops.

**Q: Security?**
A: The panel binds to localhost by default. API calls support bearer tokens over HTTPS, and every payload carries a SHA-256 so the receiver can verify integrity. Panel login and encrypted token storage are on the roadmap.

**Q: How does it scale?**
A: One lightweight instance per machine PC. The ledger is per station. A fleet dashboard aggregating stations is the next step.

**Q: Why Python and not a compiled app?**
A: Standard-library Python means no dependencies and no installer. It can still be packaged into a single .exe with PyInstaller for plants that prefer that.

**Q: What would you need to pilot it?**
A: One machine folder, the customer's MES format and endpoint (or share path), and 30 minutes.
