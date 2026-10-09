"""Turn a TripSpec into ranked itineraries.

1. Enumerate every *skeleton*: a visiting order plus a departure date for
   each leg that satisfies the stop / date constraints.
2. Price every distinct (from, to, date) leg once with every provider.
3. Each skeleton's best itinerary picks the best option per leg (legs are
   independent once dates are fixed), then skeletons are ranked.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .models import (COACH, FLIGHT, GUIDE, NIGHT_TRAIN, TRAIN, Costed, Itinerary,
                     LegPlan, Option, Place)
from .providers.base import Provider
from .tripspec import Endpoint, Stop, TripSpec

# Minutes at the terminal: get there early, get out at the other end.
DEFAULT_BUFFER = {FLIGHT: 150, TRAIN: 20, NIGHT_TRAIN: 30, COACH: 20}
# Overnight journeys: hours that count as sleep rather than lost time, and the
# share of a hotel night they replace. A coach seat is a poor night's sleep.
SLEEP_HOURS = {NIGHT_TRAIN: 6, COACH: 3}
HOTEL_SHARE = {NIGHT_TRAIN: 1.0, COACH: 0.5}

LegKey = tuple[str, str, date]


@dataclass
class SkeletonLeg:
    origins: list[Endpoint]  # one entry unless this is the first leg
    destinations: list[Endpoint]  # one entry unless this is the last leg
    depart: date
    arrive_window: tuple[date, ...] | None = None
    # For legs into a stop: the next departure and the allowed nights there,
    # checked against each option's real arrival date (night trains arrive
    # the next morning, so they leave one night fewer at the destination).
    next_depart: date | None = None
    stay: tuple[int, int] | None = None

    def accepts(self, c: Costed) -> bool:
        arrive = c.option.arrive_date
        if self.arrive_window and arrive not in self.arrive_window:
            return False
        if self.next_depart and self.stay:
            nights = (self.next_depart - arrive).days
            return self.stay[0] <= nights <= self.stay[1]
        return True


@dataclass
class PlanResult:
    spec: TripSpec
    itineraries: list[Itinerary]
    options: dict[LegKey, list[Costed]]
    places: dict[str, Place]
    skeletons: int = 0
    unpriced: list[LegKey] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def cheapest(self) -> list[Itinerary]:
        return sorted(self.itineraries, key=lambda i: i.cash)


def cost_option(opt: Option, spec: TripSpec) -> Costed | None:
    def rate(cur: str) -> float | None:
        return spec.fx.get(cur.upper())

    r_fare, r_o, r_d = rate(opt.currency), rate(opt.origin.transfer_currency), rate(
        opt.destination.transfer_currency)
    if r_fare is None or r_o is None or r_d is None:
        return None
    base = opt.typical_price if (opt.price_kind == GUIDE and opt.typical_price) else opt.price
    fare = base * r_fare * spec.travellers
    transfers = (opt.origin.transfer_cost * r_o + opt.destination.transfer_cost * r_d) * spec.travellers
    buffer = opt.buffer_minutes if opt.buffer_minutes is not None else DEFAULT_BUFFER.get(opt.mode, 30)
    door = opt.origin.transfer_minutes + buffer + opt.duration_minutes + opt.destination.transfer_minutes
    hotel_credit = spec.hotel_night * HOTEL_SHARE.get(opt.mode, 0) if opt.overnight else 0.0
    lost_hours = door / 60 - (SLEEP_HOURS.get(opt.mode, 0) if opt.overnight else 0)
    score = fare + transfers + spec.value_of_time * max(lost_hours, 0) - hotel_credit
    return Costed(opt, round(fare, 2), round(transfers, 2), door, hotel_credit, round(score, 2))


def skeletons(spec: TripSpec) -> list[list[SkeletonLeg]]:
    out: list[list[SkeletonLeg]] = []
    last_end = max(spec.end_dates) if spec.end_dates else None

    def walk(order: list[Stop], i: int, depart: date, legs: list[SkeletonLeg]):
        stop = order[i]
        # Allow the day before an arrival window too (overnight journeys);
        # options are filtered on their real arrival date when priced.
        if stop.arrive and depart not in stop.arrive and depart + timedelta(1) not in stop.arrive:
            return
        origins = spec.starts if i == 0 else [Endpoint(order[i - 1].place)]
        lo, hi = stop.nights
        # One extra day of slack lets an overnight journey still leave `hi` nights.
        for n in range(lo, hi + 2):
            nxt = depart + timedelta(days=n)
            if stop.depart and nxt not in stop.depart:
                continue
            if last_end and nxt > last_end:
                break
            here = legs + [SkeletonLeg(origins, [Endpoint(stop.place)], depart, stop.arrive,
                                       next_depart=nxt, stay=stop.nights)]
            if i == len(order) - 1:
                if spec.end_dates and nxt not in spec.end_dates:
                    continue
                out.append(here + [SkeletonLeg([Endpoint(stop.place)], spec.ends, nxt)])
            else:
                walk(order, i + 1, nxt, here)

    for order in spec.orderings():
        for d0 in spec.start_dates:
            walk(order, 0, d0, [])
    return out


def _leg_keys(skels: list[list[SkeletonLeg]]) -> dict[LegKey, tuple[Place, Place]]:
    keys: dict[LegKey, tuple[Place, Place]] = {}
    for skel in skels:
        for leg in skel:
            for o in leg.origins:
                for d in leg.destinations:
                    keys[(o.place.key, d.place.key, leg.depart)] = (o.place, d.place)
    return keys


def plan(spec: TripSpec, providers: list[Provider],
         progress: Callable[[int, int], None] | None = None, workers: int = 6) -> PlanResult:
    skels = skeletons(spec)
    keys = _leg_keys(skels)
    places = {p.key: p for pair in keys.values() for p in pair}
    warnings: list[str] = []
    missing_fx: set[str] = set()

    def price(key: LegKey) -> tuple[LegKey, list[Costed]]:
        origin, dest = keys[key]
        found: list[Option] = []
        for prov in providers:
            if not set(prov.modes) & set(spec.modes):
                continue
            try:
                found.extend(o for o in prov.search(origin, dest, key[2]) if o.mode in spec.modes)
            except Exception as exc:  # one broken provider shouldn't sink the search
                warnings.append(f"{prov.name}: {origin.name}->{dest.name} {key[2]}: {exc}")
        costed = []
        for opt in found:
            c = cost_option(opt, spec)
            if c is None:
                missing_fx.add(opt.currency)
            else:
                costed.append(c)
        costed.sort(key=lambda c: c.score)
        return key, costed

    options: dict[LegKey, list[Costed]] = {}
    ordered = sorted(keys, key=lambda k: (k[2], k[0], k[1]))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n, (key, costed) in enumerate(pool.map(price, ordered), 1):
            options[key] = costed
            if progress:
                progress(n, len(ordered))
    if missing_fx:
        warnings.append(f"No exchange rate for {sorted(missing_fx)}; add them under fx: in the trip file.")

    itineraries: dict[tuple, Itinerary] = {}
    for skel in skels:
        legs: list[LegPlan] = []
        extra_cost = extra_minutes = 0
        extra_score = 0.0
        for leg in skel:
            best: tuple[float, Endpoint, Endpoint, Costed, list[Costed]] | None = None
            for o in leg.origins:
                for d in leg.destinations:
                    key = (o.place.key, d.place.key, leg.depart)
                    cands = [c for c in options.get(key, []) if leg.accepts(c)]
                    if not cands:
                        continue
                    penalty = _extra_score(o, spec) + _extra_score(d, spec)
                    total = cands[0].score + penalty
                    if best is None or total < best[0]:
                        best = (total, o, d, cands[0], cands)
            if best is None:
                legs = []
                break
            _, o, d, choice, cands = best
            for end in (o, d):  # only trip start/end points carry extras
                extra_cost += end.extra_cost
                extra_minutes += end.extra_minutes
                extra_score += _extra_score(end, spec)
            legs.append(LegPlan(o.place, d.place, leg.depart, choice, _alternatives(choice, cands)))
        if not legs:
            continue
        itin = Itinerary(legs, extra_cost, extra_minutes, extra_score)
        sig = itin.signature()
        if sig not in itineraries or itin.score < itineraries[sig].score:
            itineraries[sig] = itin

    ranked = sorted(itineraries.values(), key=lambda i: (i.score, i.cash, i.door_minutes))
    unpriced = sorted(k for k, cands in options.items() if not cands)
    return PlanResult(spec, ranked, options, places, len(skels), unpriced, warnings)


def _extra_score(end: Endpoint, spec: TripSpec) -> float:
    return end.extra_cost + spec.value_of_time * end.extra_minutes / 60


def _alternatives(choice: Costed, cands: list[Costed]) -> list[Costed]:
    """Best option of each other mode, plus the cheapest-cash one if different."""
    seen = {choice.option.mode}
    alts = []
    for c in cands:
        if c.option.mode not in seen:
            seen.add(c.option.mode)
            alts.append(c)
    cheapest = min(cands, key=lambda c: c.cash)
    if cheapest is not choice and cheapest not in alts:
        alts.insert(0, cheapest)
    return alts


def date_grid(result: PlanResult) -> dict[tuple[str, str], dict[date, Costed]]:
    """Cheapest option (by cash) per route per date: the 'which day?' view."""
    grid: dict[tuple[str, str], dict[date, Costed]] = defaultdict(dict)
    for (o, d, day), cands in result.options.items():
        if cands:
            grid[(o, d)][day] = min(cands, key=lambda c: c.cash)
    return dict(grid)
