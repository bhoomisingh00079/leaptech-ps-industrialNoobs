"""YSUP-Link material verification API and payload builders.

Implements the YSUP-Link OpenAPI SetupRequest and MaterialRequest contracts.
The machine and YSUP-Link remain responsible for all machine-side communication.
"""
import csv
import io
import json
from datetime import datetime


ALIASES = {
    "part_number": ("part_number", "partnumber", "part", "cmpid", "component", "material_part_number"),
    "reel_id": ("reel_id", "reelid", "reel", "uniqueid", "material_id"),
    "lot_id": ("lot_id", "lotid", "lot", "batch", "batch_id"),
    "quantity": ("quantity", "qty", "remainqty", "remainingquantity", "stock"),
}


def _key(value):
    return "".join(c.lower() for c in str(value) if c.isalnum())


def parse_database_csv(content):
    """Parse the standard header-based material CSV; extra customer columns are retained."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("Database CSV must have a header row")
    mapping = {}
    for field in reader.fieldnames:
        normalized = _key(field)
        for target, aliases in ALIASES.items():
            if normalized in {_key(alias) for alias in aliases} and target not in mapping:
                mapping[target] = field
    if "part_number" not in mapping:
        raise ValueError("Database CSV needs a Part Number column (Part Number, PartNumber, Part, or CmpID)")
    records = []
    for raw in reader:
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        records.append({"part_number": row.get(mapping["part_number"], ""),
                        "reel_id": row.get(mapping.get("reel_id", ""), ""),
                        "lot_id": row.get(mapping.get("lot_id", ""), ""),
                        "quantity": row.get(mapping.get("quantity", ""), ""),
                        "extra": row})
    if not records:
        raise ValueError("Database CSV has no material records")
    return records, mapping


def _enabled(cfg, field):
    value = cfg.get("ysup_fields", {}).get(field, True)
    return bool(value)


def _matches(material, db_row, cfg):
    checks = []
    for field in ("part_number", "reel_id", "lot_id"):
        scanned = str(material.get(field, "")).strip()
        if _enabled(cfg, field) and db_row.get(field) and scanned:
            checks.append(scanned.casefold() == db_row[field].casefold())
    for field in cfg.get("ysup_extra_fields", []):
        if field and db_row.get("extra", {}).get(field):
            checks.append(str(material.get("extra", {}).get(field, "")).strip().casefold() ==
                          db_row["extra"][field].casefold())
    return bool(checks) and all(checks)


def _timestamp():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]


def _required(obj, names, context):
    if not isinstance(obj, dict):
        raise ValueError(f"{context} must be an object")
    missing = [name for name in names if name not in obj]
    if missing:
        raise ValueError(f"{context} is missing required field(s): {', '.join(missing)}")


def _validate_machine(machine):
    _required(machine, ("MchName", "MchSerial", "MchModel", "LaneNo", "LineName"), "MchInfo")


def verify(payload, database, cfg):
    """Return a response object matching the corresponding YSUP Link schema."""
    if not isinstance(payload, dict) or not isinstance(payload.get("FuncName"), str):
        raise ValueError("Request must be a JSON object with FuncName")
    func = payload["FuncName"]
    if func == "SetupRequest":
        _required(payload, ("TraceDate", "MchInfo", "SetInfos"), "SetupRequest")
        _validate_machine(payload["MchInfo"])
        set_infos = payload.get("SetInfos")
        if not isinstance(set_infos, list):
            raise ValueError("SetupRequest.SetInfos must be an array")
        response_infos = []
        for info in set_infos:
            _required(info, ("CarrierInfos", "LocationID", "Operation"), "SetInfos entry")
            if not isinstance(info, dict) or not isinstance(info.get("CarrierInfos"), list):
                raise ValueError("Each SetInfos entry must contain CarrierInfos")
            carriers = []
            for carrier in info["CarrierInfos"]:
                _required(carrier, ("Name", "SetNo", "Type", "UniqueID", "CmpInfos"), "CarrierInfos entry")
                if not isinstance(carrier, dict):
                    raise ValueError("CarrierInfos entries must be objects")
                cmp_infos = carrier.get("CmpInfos") or []
                ok = bool(cmp_infos)
                for item in cmp_infos:
                    _required(item, ("CmpID", "Qty", "UniqueID"), "CmpInfos entry")
                    material = {"part_number": item.get("CmpID", ""),
                                "reel_id": item.get("UniqueID", ""), "lot_id": "", "extra": item}
                    ok = ok and any(_matches(material, row, cfg) for row in database)
                carriers.append({"SetNo": carrier.get("SetNo", 0), "Type": carrier.get("Type", 0),
                                 "Result": 0 if ok else 1})
            response_infos.append({"Operation": info.get("Operation", 0), "CarrierInfos": carriers})
        return {"FuncName": func, "TraceDate": payload.get("TraceDate") or _timestamp(),
                "MchInfo": payload.get("MchInfo", {}),
                "Message": "Material verification successful" if all(
                    c["Result"] == 0 for s in response_infos for c in s["CarrierInfos"]
                ) else "Material verification failed", "SetInfos": response_infos}
    if func == "MaterialRequest":
        _required(payload, ("TraceDate", "MchInfo", "Component"), "MaterialRequest")
        _validate_machine(payload["MchInfo"])
        component = payload.get("Component") or {}
        _required(component, ("ReelID", "OrderID", "Assy", "LotID", "SurfaceInfo", "BoardName"),
                  "MaterialRequest.Component")
        material = {"part_number": component.get("CmpID", ""), "reel_id": component.get("ReelID", ""),
                    "lot_id": component.get("LotID", ""), "extra": component}
        row = next((r for r in database if _matches(material, r, cfg)), None)
        result = row is not None
        quantity = component.get("Qty") if component.get("Qty") is not None else (row or {}).get("quantity", 0)
        try:
            quantity = int(float(str(quantity).replace(",", "")))
        except (ValueError, TypeError):
            quantity = 0
        return {"FuncName": func, "TraceDate": payload.get("TraceDate") or _timestamp(),
                "MchInfo": payload.get("MchInfo", {}), "Err": 0 if result else 1,
                "ErrMsg": "" if result else "Scanned material does not match the imported database",
                "Component": {"ReelID": component.get("ReelID", ""),
                              "OrderID": component.get("OrderID", "0"),
                              "CmpID": row["part_number"] if row else component.get("CmpID", ""),
                              "LotID": row["lot_id"] if row else component.get("LotID", ""),
                              "Qty": quantity, "Rank": component.get("Rank", [])}}
    raise ValueError(f"Unsupported YSUP FuncName: {func}")


def remote_scan_payload(machine, operation, result, params):
    """Build the YSUP REST-MES MachineOperationCommand REMOTE_SCAN request."""
    return {"FuncName": "MachineOperationCommand", "TraceDate": _timestamp(),
            "CommandInfo": {"TargetMachine": machine, "TargetOperation": "REMOTE_SCAN",
                            "Params": {"Operation": int(operation), "RemoteResult": "OK" if result else "NG",
                                       "Params": [str(value) for value in params]}}}


def remote_scan_component_payload(machine, result, set_no, component, database):
    """Build a REMOTE_SCAN message for one scanner result."""
    part = str(component.get("CmpID", ""))
    reel = str(component.get("UniqueID", ""))
    quantity = str(component.get("Qty", ""))
    row = next((r for r in database if r.get("part_number", "").casefold() == part.casefold()
                and r.get("reel_id", "").casefold() == reel.casefold()), {})
    lot = str(component.get("LotID") or row.get("lot_id", ""))
    # Scanner barcode example in the requirements: Part Number$Reel ID$Quantity.
    params = ["0", part + "$" + reel + "$" + quantity, str(set_no), part, reel, lot, quantity]
    params.extend([""] * max(0, 12 - len(params)))
    return remote_scan_payload(machine, 0, result, params)
