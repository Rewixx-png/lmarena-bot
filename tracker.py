"""Diff engine: compare two model snapshots and emit change events."""
from dataclasses import dataclass, field

NEW_MODEL = "NEW_MODEL"
RENAMED = "RENAMED"
DEANONYMIZED = "DEANONYMIZED"
REMOVED = "REMOVED"
STATS_UPDATE = "STATS_UPDATE"


@dataclass
class Event:
    type: str
    model_key: str
    display_name: str
    old: dict | None = None
    new: dict | None = None
    extra: dict = field(default_factory=dict)


def _find_deanonymized(old: dict, candidates: dict[str, dict], consumed: set[str]):
    """Heuristic: an anonymous model vanished and a newly-appeared model with
    ~same votes/Elo took its place -> the anonymous bot was revealed."""
    ovotes = old.get("votes") or 0
    orating = old.get("rating") or 0.0
    for key, n in candidates.items():
        if key in consumed:
            continue
        nvotes = n.get("votes") or 0
        nrating = n.get("rating") or 0.0
        votes_close = abs(nvotes - ovotes) <= max(50, ovotes * 0.02)
        rating_close = abs(nrating - orating) <= 3.0
        if votes_close and rating_close:
            return key
    return None


def _stats_changed(old: dict, new: dict, settings) -> bool:
    rank = new.get("rank")
    if rank is not None and rank > settings.stats_notify_top_n:
        return False
    if abs((old.get("rating") or 0.0) - (new.get("rating") or 0.0)) >= settings.elo_change_threshold:
        return True
    orank, nrank = old.get("rank"), new.get("rank")
    if orank is not None and nrank is not None and abs(orank - nrank) >= settings.rank_change_threshold:
        return True
    return False


def diff(old: dict[str, dict], new: dict[str, dict], settings) -> list[Event]:
    """Return change events between two snapshots keyed by model_key.

    `old` empty -> first run (baseline): return [] (nothing to notify).
    """
    if not old:
        return []

    events: list[Event] = []
    old_keys, new_keys = set(old), set(new)
    added_keys = new_keys - old_keys
    consumed_new: set[str] = set()

    # 1. Removed models (possibly de-anonymized with a key change).
    for key in sorted(old_keys - new_keys):
        o = old[key]
        if o.get("is_anonymous"):
            match = _find_deanonymized(o, {k: new[k] for k in added_keys}, consumed_new)
            if match:
                consumed_new.add(match)
                n = new[match]
                events.append(Event(
                    DEANONYMIZED, match, n["display_name"],
                    old=o, new=n,
                    extra={"old_key": key, "old_name": o["display_name"]},
                ))
                continue
        events.append(Event(REMOVED, key, o["display_name"], old=o))

    # 2. New models.
    for key in sorted(new_keys - old_keys):
        if key in consumed_new:
            continue
        events.append(Event(NEW_MODEL, key, new[key]["display_name"], new=new[key]))

    # 3. Common keys: rename / de-anonymization / stats update.
    for key in sorted(old_keys & new_keys):
        o, n = old[key], new[key]
        if o.get("display_name") != n.get("display_name"):
            if o.get("is_anonymous") and not n.get("is_anonymous"):
                events.append(Event(DEANONYMIZED, key, n["display_name"], old=o, new=n))
            else:
                events.append(Event(RENAMED, key, n["display_name"], old=o, new=n))
            continue
        if _stats_changed(o, n, settings):
            events.append(Event(STATS_UPDATE, key, n["display_name"], old=o, new=n))

    return events
