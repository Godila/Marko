from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from mpmt.journal.models import Event, Item
from mpmt.journal.state import transition


def apply_event(db: Session, *, source: str, source_event_id: str, kind: str,
                km: str, srid: str, payload: dict) -> tuple[str, bool]:
    stmt = (pg_insert(Event)
            .values(source=source, source_event_id=source_event_id, kind=kind,
                    km=km, srid=srid, payload=payload)
            .on_conflict_do_nothing(constraint="uq_source_event")
            .returning(Event.id))
    if not db.execute(stmt).scalar():
        db.commit()
        item = db.get(Item, km)
        return (item.state if item else "NEW"), False
    item = db.get(Item, km)
    new_state = transition(item.state if item else "NEW", kind)
    if item is None:
        db.add(Item(km=km, state=new_state, last_event=payload))
    else:
        item.state = new_state
        item.last_event = payload
    db.commit()
    return new_state, True
