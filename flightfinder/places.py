"""Load the places table (cities and their airports / stations)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from .models import Place, Terminal


def _read_yaml(path: Path | None, default_name: str):
    if path is None:
        text = resources.files("flightfinder.data").joinpath(default_name).read_text("utf-8")
    else:
        text = Path(path).read_text("utf-8")
    return yaml.safe_load(text) or {}


def load_places(extra: Path | None = None) -> dict[str, Place]:
    raw = _read_yaml(None, "places.yaml")
    if extra is not None:
        raw.update(_read_yaml(extra, ""))
    places: dict[str, Place] = {}
    for key, spec in raw.items():
        currency = spec.get("currency", "EUR")
        terminals = tuple(
            Terminal(
                code=t["code"],
                name=t["name"],
                kind=t["kind"],
                place=key,
                transfer_minutes=int(t.get("minutes", 30)),
                transfer_cost=float(t.get("cost", 0)),
                transfer_currency=t.get("currency", currency),
            )
            for t in spec.get("terminals", [])
        )
        places[key] = Place(key=key, name=spec.get("name", key.title()),
                            country=spec.get("country", ""), terminals=terminals)
    return places


def resolve(name: str, places: dict[str, Place]) -> Place:
    """Find a place by key, display name or one of its terminal codes."""
    needle = name.strip().lower()
    if needle in places:
        return places[needle]
    for place in places.values():
        if place.name.lower() == needle:
            return place
        if any(t.code.lower() == needle for t in place.terminals):
            return place
    known = ", ".join(sorted(places))
    raise KeyError(f"Unknown place {name!r}. Known places: {known}. "
                   "Add it to a places file and pass --places.")
