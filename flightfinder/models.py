"""Core data types shared by providers, the planner and the report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

FLIGHT = "flight"
TRAIN = "train"
NIGHT_TRAIN = "night-train"
COACH = "coach"
MODES = (FLIGHT, TRAIN, NIGHT_TRAIN, COACH)

# Live prices come from an API/scraper, guide prices from the curated route
# table, manual prices from a quotes file the user filled in.
LIVE = "live"
GUIDE = "guide"
MANUAL = "manual"


# Standard-time UTC offsets (hours). Europe changes clocks on the same dates,
# so the difference between two countries is constant; anything unlisted is CET.
_UTC_OFFSET = {"GB": 0, "IE": 0, "PT": 0, "IS": 0, "GR": 2, "FI": 2, "EE": 2,
               "LV": 2, "LT": 2, "RO": 2, "BG": 2, "CY": 2, "UA": 2}


def clock_shift_minutes(origin_country: str, destination_country: str) -> int:
    """How far the destination's clock is ahead of the origin's."""
    return 60 * (_UTC_OFFSET.get(destination_country, 1) - _UTC_OFFSET.get(origin_country, 1))


@dataclass(frozen=True)
class Terminal:
    """An airport, railway station or coach station belonging to a place."""

    code: str
    name: str
    kind: str  # airport | station | coach
    place: str
    transfer_minutes: int = 30  # city centre <-> terminal
    transfer_cost: float = 0.0
    transfer_currency: str = "GBP"


@dataclass(frozen=True)
class Place:
    key: str
    name: str
    country: str
    terminals: tuple[Terminal, ...]

    @property
    def airports(self) -> tuple[Terminal, ...]:
        return tuple(t for t in self.terminals if t.kind == "airport")

    def terminal(self, code: str) -> Terminal | None:
        code = code.upper()
        return next((t for t in self.terminals if t.code.upper() == code), None)

    def main_terminal(self, kind: str) -> Terminal | None:
        return next((t for t in self.terminals if t.kind == kind), None)


@dataclass
class Option:
    """One way of getting from A to B on a given day, with a price."""

    mode: str
    carrier: str
    origin: Terminal
    destination: Terminal
    depart_date: date
    price: float
    currency: str
    duration_minutes: int
    source: str
    price_kind: str = LIVE
    depart_time: str | None = None
    arrive_time: str | None = None
    arrive_day_offset: int = 0
    stops: int = 0
    booking_url: str | None = None
    notes: str = ""
    # Minutes to allow at the terminal (security, check-in, boarding) plus
    # leaving it at the other end. None means "use the default for the mode".
    buffer_minutes: int | None = None
    # Guide prices are ranges; price is the low end, typical the usual cost.
    typical_price: float | None = None

    @property
    def arrive_date(self) -> date:
        return self.depart_date + timedelta(days=self.arrive_day_offset)

    @property
    def overnight(self) -> bool:
        """True when you sleep on board, saving a night's accommodation."""
        return self.mode in (NIGHT_TRAIN, COACH) and self.arrive_day_offset > 0

    @property
    def is_ground(self) -> bool:
        return self.mode != FLIGHT


@dataclass
class Costed:
    """An option with everything converted into the trip's currency."""

    option: Option
    fare: float
    transfers: float
    door_minutes: int
    hotel_credit: float
    score: float  # generalised cost: cash + value of time - hotel saved

    @property
    def cash(self) -> float:
        return self.fare + self.transfers


@dataclass
class LegPlan:
    origin: Place
    destination: Place
    depart_date: date
    choice: Costed
    alternatives: list[Costed] = field(default_factory=list)


@dataclass
class Itinerary:
    legs: list[LegPlan]
    extra_cost: float = 0.0  # e.g. getting to London first
    extra_minutes: int = 0
    extra_score: float = 0.0  # extra_cost plus the value of extra_minutes

    @property
    def cash(self) -> float:
        return sum(leg.choice.cash for leg in self.legs) + self.extra_cost

    @property
    def score(self) -> float:
        return sum(leg.choice.score for leg in self.legs) + self.extra_score

    @property
    def door_minutes(self) -> int:
        return sum(leg.choice.door_minutes for leg in self.legs) + self.extra_minutes

    @property
    def has_unpriced_guides(self) -> bool:
        return any(leg.choice.option.price_kind == GUIDE for leg in self.legs)

    def signature(self) -> tuple:
        return tuple(
            (leg.origin.key, leg.destination.key, leg.depart_date) for leg in self.legs
        )
