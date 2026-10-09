"""Parse trip files (YAML) describing a multi-stop trip with flexible dates.

Example::

    name: Berlin, Amsterdam & Barcelona
    currency: GBP
    start:
      from: [bristol, london]
      depart: [2026-10-26, 2026-10-27]
    stops:
      - {place: berlin, nights: 1-3, group: north}
      - {place: amsterdam, nights: 1-3, group: north}   # same group = any order
      - {place: barcelona, arrive: [2026-10-30, 2026-10-31]}
    end:
      to: [bristol, london]
      depart: [2026-11-05, 2026-11-06]
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import yaml

from .models import MODES, Place
from .places import resolve


@dataclass
class Endpoint:
    place: Place
    extra_cost: float = 0.0  # e.g. the cost of getting to London from home
    extra_minutes: int = 0


@dataclass
class Stop:
    place: Place
    nights: tuple[int, int] = (1, 14)
    arrive: tuple[date, ...] | None = None  # allowed arrival dates
    depart: tuple[date, ...] | None = None  # allowed departure dates
    group: str | None = None


@dataclass
class TripSpec:
    name: str
    currency: str
    starts: list[Endpoint]
    start_dates: tuple[date, ...]
    stops: list[Stop]
    ends: list[Endpoint]
    end_dates: tuple[date, ...]
    travellers: int = 1
    modes: tuple[str, ...] = MODES
    value_of_time: float = 10.0  # per hour of door-to-door travel
    hotel_night: float = 70.0  # credited when you sleep on a night train/coach
    fx: dict[str, float] = field(default_factory=dict)  # 1 unit -> trip currency
    max_stops: int = 1  # flight connections allowed
    notes: list[str] = field(default_factory=list)

    def orderings(self) -> list[list[Stop]]:
        """Every visiting order allowed by the stop groups.

        Consecutive stops sharing a `group` may be visited in any order;
        everything else keeps the order written in the file.
        """
        blocks: list[list[Stop]] = []
        for stop in self.stops:
            if blocks and stop.group and blocks[-1][0].group == stop.group:
                blocks[-1].append(stop)
            else:
                blocks.append([stop])
        per_block = [list(itertools.permutations(b)) for b in blocks]
        return [
            [s for block in combo for s in block] for combo in itertools.product(*per_block)
        ]


_DATE_RANGE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:\.\.|to)\s*(\d{4}-\d{2}-\d{2})\s*$")


def parse_dates(value) -> tuple[date, ...]:
    """Accept a date, a list of dates, or 'YYYY-MM-DD..YYYY-MM-DD'."""
    if value is None:
        return ()
    if isinstance(value, date):
        return (value,)
    if isinstance(value, str):
        m = _DATE_RANGE.match(value)
        if m:
            lo, hi = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
            if hi < lo:
                raise ValueError(f"Date range ends before it starts: {value}")
            return tuple(lo + timedelta(days=i) for i in range((hi - lo).days + 1))
        return (date.fromisoformat(value.strip()),)
    if isinstance(value, (list, tuple)):
        out: list[date] = []
        for v in value:
            out.extend(parse_dates(v))
        return tuple(sorted(set(out)))
    raise ValueError(f"Can't read dates from {value!r}")


def parse_nights(value) -> tuple[int, int]:
    if value is None:
        return (1, 14)
    if isinstance(value, int):
        return (value, value)
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(value))
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            raise ValueError(f"nights range is backwards: {value}")
        return (lo, hi)
    n = int(value)
    return (n, n)


_RESTRICT = re.compile(r"^\s*([^\[\]]+?)\s*(?:\[([^\]]*)\])?\s*$")


def parse_place(text: str, places: dict[str, Place]) -> Place:
    """'london' or 'london[LGW,STN]' (limit which airports are searched)."""
    m = _RESTRICT.match(text)
    if not m:
        raise ValueError(f"Can't read place {text!r}")
    place = resolve(m.group(1), places)
    if m.group(2):
        keep = {c.strip().upper() for c in m.group(2).split(",") if c.strip()}
        unknown = keep - {t.code.upper() for t in place.terminals}
        if unknown:
            raise ValueError(f"{place.name} has no terminal(s) {sorted(unknown)}")
        terminals = tuple(
            t for t in place.terminals if t.kind != "airport" or t.code.upper() in keep
        )
        place = replace(place, terminals=terminals)
    return place


def _endpoints(value, places) -> list[Endpoint]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(Endpoint(parse_place(item["place"], places),
                                float(item.get("extra_cost", 0)),
                                int(item.get("extra_minutes", 0))))
        else:
            out.append(Endpoint(parse_place(str(item), places)))
    return out


def load_trip(path: Path | str, places: dict[str, Place]) -> TripSpec:
    raw = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    return trip_from_dict(raw, places)


def trip_from_dict(raw: dict, places: dict[str, Place]) -> TripSpec:
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    starts = _endpoints(start.get("from"), places)
    if not starts:
        raise ValueError("start.from is required (one or more places)")
    ends = _endpoints(end.get("to"), places) or starts
    start_dates = parse_dates(start.get("depart"))
    if not start_dates:
        raise ValueError("start.depart is required (date, list or range)")
    end_dates = parse_dates(end.get("depart"))

    stops = []
    for s in raw.get("stops") or []:
        stops.append(Stop(
            place=parse_place(str(s["place"]), places),
            nights=parse_nights(s.get("nights")),
            arrive=parse_dates(s.get("arrive")) or None,
            depart=parse_dates(s.get("depart")) or None,
            group=s.get("group"),
        ))
    if not stops:
        raise ValueError("A trip needs at least one stop")

    modes = tuple(raw.get("modes") or MODES)
    bad = set(modes) - set(MODES)
    if bad:
        raise ValueError(f"Unknown modes {sorted(bad)}; choose from {MODES}")

    currency = raw.get("currency", "GBP").upper()
    fx = {"GBP": 1.0, "EUR": 0.87, "USD": 0.75} if currency == "GBP" else {currency: 1.0}
    fx.update({k.upper(): float(v) for k, v in (raw.get("fx") or {}).items()})
    fx[currency] = 1.0

    notes = raw.get("notes") or []
    return TripSpec(
        name=raw.get("name", "Trip"),
        currency=currency,
        starts=starts,
        start_dates=start_dates,
        stops=stops,
        ends=ends,
        end_dates=end_dates,
        travellers=int(raw.get("travellers", 1)),
        modes=modes,
        value_of_time=float(raw.get("value_of_time", 10)),
        hotel_night=float(raw.get("hotel_night", 70)),
        fx=fx,
        max_stops=int(raw.get("max_stops", 1)),
        notes=[notes] if isinstance(notes, str) else list(notes),
    )
