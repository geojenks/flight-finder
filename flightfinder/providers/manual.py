"""Prices you found yourself (any site, any airline), from a quotes file.

Useful for airlines no provider covers well (e.g. easyJet direct), or to
pin a fare you've seen. Example ``quotes.yaml``::

    - {from: BRS, to: BER, date: 2026-10-26, price: 46, currency: GBP,
       carrier: easyJet, depart: "06:45", duration: 1h55}
    - {from: berlin, to: amsterdam, date: 2026-10-28, price: 39, currency: EUR,
       mode: train, carrier: ICE, duration: 7h, url: https://int.bahn.de}

`from` / `to` may be a terminal code (BRS, STP...) or a place key.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from ..models import FLIGHT, MANUAL, Option, Place
from .base import Provider
from .ground import parse_duration


class ManualProvider(Provider):
    name = "manual"
    modes = ("flight", "train", "night-train", "coach")

    def __init__(self, path: Path | str | None = None, quotes: list[dict] | None = None):
        if quotes is None:
            quotes = (yaml.safe_load(Path(path).read_text("utf-8")) or []) if path else []
        self.quotes = quotes

    @staticmethod
    def _terminal(place: Place, ref: str, mode: str):
        if ref.lower() == place.key:
            kind = "airport" if mode == FLIGHT else ("coach" if mode == "coach" else "station")
            return place.main_terminal(kind) or (place.terminals[0] if place.terminals else None)
        return place.terminal(ref)

    def search(self, origin: Place, destination: Place, day: date) -> list[Option]:
        out = []
        for q in self.quotes:
            qday = q["date"] if isinstance(q["date"], date) else date.fromisoformat(str(q["date"]))
            if qday != day:
                continue
            mode = q.get("mode", FLIGHT)
            o = self._terminal(origin, str(q["from"]), mode)
            d = self._terminal(destination, str(q["to"]), mode)
            if o is None or d is None:
                continue
            out.append(Option(
                mode=mode,
                carrier=q.get("carrier", "?"),
                origin=o,
                destination=d,
                depart_date=day,
                price=float(q["price"]),
                currency=str(q.get("currency", "GBP")).upper(),
                duration_minutes=parse_duration(q.get("duration", "2h")),
                source=self.name,
                price_kind=MANUAL,
                depart_time=q.get("depart"),
                arrive_time=q.get("arrive"),
                arrive_day_offset=int(q.get("arrive_day_offset", 0)),
                stops=int(q.get("stops", 0)),
                booking_url=q.get("url"),
                notes=q.get("notes", ""),
                buffer_minutes=q.get("buffer"),
            ))
        return out
