"""Train, night-train and coach options from the curated route table."""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path

import yaml

from ..models import GUIDE, Option, Place
from .base import Provider

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_duration(text) -> int:
    """'4h05', '11h', '45m' or minutes as a number -> minutes."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m?)?\s*", str(text))
    if not m or not (m.group(1) or m.group(2)):
        raise ValueError(f"Can't read duration {text!r}")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def load_routes(extra: Path | None = None) -> list[dict]:
    text = resources.files("flightfinder.data").joinpath("ground_routes.yaml").read_text("utf-8")
    routes = list(yaml.safe_load(text) or [])
    if extra is not None:
        routes.extend(yaml.safe_load(Path(extra).read_text("utf-8")) or [])
    out = []
    for r in routes:
        out.append(r)
        if r.get("both_ways"):
            back = dict(r)
            back.update({
                "id": r["id"] + "-rev",
                "from": r["to"], "to": r["from"],
                "from_terminal": r["to_terminal"], "to_terminal": r["from_terminal"],
            })
            out.append(back)
    return out


def _runs_on(route: dict, day: date) -> bool:
    days = route.get("days")
    if days and _DAYS[day.weekday()] not in [d.lower()[:3] for d in days]:
        return False
    valid = route.get("valid")
    if valid:
        for window in valid:
            lo, hi = window.get("from"), window.get("to")
            if (lo is None or day >= _as_date(lo)) and (hi is None or day <= _as_date(hi)):
                return True
        return False
    return True


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


class GroundProvider(Provider):
    name = "ground"
    modes = ("train", "night-train", "coach")

    def __init__(self, routes: list[dict] | None = None):
        self.routes = routes if routes is not None else load_routes()

    def search(self, origin: Place, destination: Place, day: date) -> list[Option]:
        out = []
        for r in self.routes:
            if r["from"] != origin.key or r["to"] != destination.key or not _runs_on(r, day):
                continue
            o = origin.terminal(r["from_terminal"])
            d = destination.terminal(r["to_terminal"])
            if o is None or d is None:
                continue
            price = r.get("price") or {}
            notes = r.get("notes", "").strip()
            if r.get("verify"):
                notes = ("CHECK SCHEDULE. " + notes).strip()
            out.append(Option(
                mode=r["mode"],
                carrier=r.get("operator", ""),
                origin=o,
                destination=d,
                depart_date=day,
                price=float(price.get("from", price.get("typical", 0))),
                typical_price=float(price.get("typical", price.get("from", 0))),
                currency=price.get("currency", "EUR"),
                duration_minutes=parse_duration(r["duration"]),
                source=self.name,
                price_kind=GUIDE,
                depart_time=r.get("depart"),
                arrive_time=r.get("arrive"),
                arrive_day_offset=int(r.get("arrive_day_offset", 0)),
                booking_url=r.get("url"),
                notes=notes,
                buffer_minutes=r.get("buffer"),
            ))
        return out
