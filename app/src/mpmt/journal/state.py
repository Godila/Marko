RULES = {
    ("NEW", "sale"): "PENDING_WITHDRAW",
    ("NEW", "return"): "ANOMALY_NO_RECEIPT",
    ("NEW", "skip_fbw"): "SKIPPED_FBW",
    ("PENDING_WITHDRAW", "sale"): "ANOMALY_RESALE",
    ("PENDING_WITHDRAW", "return"): "PENDING_RETURN",
    ("WITHDRAWN", "return"): "PENDING_RETURN",
    ("PENDING_RETURN", "sale"): "PENDING_WITHDRAW",
    ("PENDING_RETURN", "return"): "ANOMALY_RERETURN",
    ("RETURNED", "sale"): "PENDING_WITHDRAW",
}


def transition(state: str, kind: str) -> str:
    return RULES.get((state, kind), "ANOMALY_UNKNOWN_TRANSITION")
