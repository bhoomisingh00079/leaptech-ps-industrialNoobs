"""YSUP-Link response shapes. Setup and material verification are intentionally deferred to M4."""
import copy

PLACEHOLDER = "Verification is not configured yet"
SIMPLE = frozenset((
    "CmpStatusReport", "DetectedPartsKnotReport", "MaintenanceReport", "MchStatusChange", "PgmSwitched",
    "SetupRemoved", "SensorPartsRemainCorrection", "DisplayMessageResult", "MachineOperationCommandResult",
))
KNOWN = SIMPLE | frozenset(("PcbEntryEvent", "PcbExitEvent", "SetupRequest", "MaterialRequest"))


def _mch(request):
    return copy.deepcopy(request.get("MchInfo", {}))


def _pcb_info(request):
    value = request.get("PcbInfo", {})
    if not isinstance(value, dict):
        return {}
    return {key: copy.deepcopy(value[key]) for key in ("PcbID", "PgmName") if key in value}


def _blocks(request):
    value = request.get("Blk", [])
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [{"BlkNo": copy.deepcopy(item.get("BlkNo")), "Mount": copy.deepcopy(item.get("Mount", 1))}
            for item in value if isinstance(item, dict)]


def _setup(request):
    response = copy.deepcopy(request)
    response["FuncName"] = "SetupRequest"

    def refuse(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.casefold() in ("carrier", "carriers"):
                    carriers = value if isinstance(value, list) else [value]
                    for carrier in carriers:
                        if isinstance(carrier, dict):
                            carrier["Result"] = 1
                            carrier["Message"] = PLACEHOLDER
            for value in node.values():
                refuse(value)
        elif isinstance(node, list):
            for value in node:
                refuse(value)

    refuse(response)
    return response


def build(request):
    """Return a safe, spec-shaped response for a known FuncName."""
    name = request["FuncName"]
    if name in SIMPLE:
        return {"FuncName": name}
    if name == "PcbEntryEvent":
        return {"FuncName": name, "TraceDate": request["TraceDate"], "MchInfo": _mch(request), "Err": 0,
                "ErrMsg": "", "PcbInfo": _pcb_info(request), "Blk": _blocks(request)}
    if name == "PcbExitEvent":
        return {"FuncName": name, "TraceDate": request["TraceDate"], "MchInfo": _mch(request), "Err": 0,
                "ErrMsg": "", "PcbInfo": _pcb_info(request)}
    if name == "SetupRequest":
        return _setup(request)
    if name == "MaterialRequest":
        return {"FuncName": name, "Err": 1, "ErrMsg": PLACEHOLDER}
    raise KeyError(name)
