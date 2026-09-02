from sqlalchemy.orm import Session

from mpmt.journal import apply_event


def excise_rows_to_events(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        kind = "sale" if row["operation_type_id"] == 1 else "return"
        out.append({
            "source": "wb_excise",
            "source_event_id": f"{row['srid']}:{row['excise_short']}:{row['operation_type_id']}",
            "kind": kind,
            "km": row["excise_short"],
            "srid": row["srid"],
            "payload": row,
        })
    return out


def fbs_rids(order_rows: list[dict]) -> set[str]:
    return {o["rid"] for o in order_rows if o.get("rid")}


def ingest_excise(db: Session, rows: list[dict], fbs: set[str]) -> dict:
    stats = {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 0}
    for ev in excise_rows_to_events(rows):
        if ev["srid"] not in fbs:
            _, created = apply_event(db, source="wb_excise",
                                     source_event_id=ev["source_event_id"],
                                     kind="skip_fbw", km=ev["km"], srid=ev["srid"],
                                     payload=ev["payload"])
            if created:
                stats["skipped_fbw"] += 1
            else:
                stats["duplicates"] += 1
            continue
        _, created = apply_event(db, **ev)
        if created:
            stats[ev["kind"]] += 1
        else:
            stats["duplicates"] += 1
    return stats
