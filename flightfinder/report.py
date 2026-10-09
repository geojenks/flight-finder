"""Render a PlanResult as Markdown (readable in a terminal, too)."""

from __future__ import annotations

from datetime import date

from .models import FLIGHT, GUIDE, LIVE, MANUAL, Costed, Itinerary
from .planner import PlanResult, date_grid

SYMBOL = {"GBP": "£", "EUR": "€", "USD": "$"}
MODE_LABEL = {"flight": "fly", "train": "train", "night-train": "night train", "coach": "coach"}
KIND_MARK = {LIVE: "", GUIDE: "~", MANUAL: "*"}


def money(value: float, currency: str) -> str:
    sym = SYMBOL.get(currency)
    return f"{sym}{value:,.0f}" if sym else f"{value:,.0f} {currency}"


def hours(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def day(d: date) -> str:
    return d.strftime("%a %d %b").replace(" 0", " ")


def _price(c: Costed, cur: str) -> str:
    return KIND_MARK[c.option.price_kind] + money(c.fare, cur)


def _times(c: Costed) -> str:
    o = c.option
    if not o.depart_time:
        return ""
    arr = o.arrive_time or "?"
    if o.arrive_day_offset:
        arr += f" (+{o.arrive_day_offset})"
    return f"{o.depart_time}–{arr}"


def _route_line(it: Itinerary, cur: str) -> str:
    parts = []
    for leg in it.legs:
        o = leg.choice.option
        parts.append(f"{day(leg.depart_date)} {o.origin.code}→{o.destination.code} "
                     f"{MODE_LABEL[o.mode]} {_price(leg.choice, cur)}")
    return " · ".join(parts)


def _stay_line(it: Itinerary) -> str:
    stays = []
    for prev, nxt in zip(it.legs, it.legs[1:]):
        nights = (nxt.depart_date - prev.choice.option.arrive_date).days
        stays.append(f"{prev.destination.name} {nights}n")
    return ", ".join(stays)


def _verdict(choice: Costed, alt: Costed, cur: str) -> str:
    dc = alt.cash - choice.cash
    dt = alt.door_minutes - choice.door_minutes
    bits = [f"{money(abs(dc), cur)} {'more' if dc > 0 else 'less'}" if round(dc) else "same price"]
    if abs(dt) >= 15:
        bits.append(f"{hours(abs(dt))} {'slower' if dt > 0 else 'faster'} door to door")
    if alt.option.overnight:
        bits.append("saves a hotel night")
    if alt.option.is_ground and not choice.option.is_ground:
        bits.append("city centre to city centre, no airport security")
    return ", ".join(bits)


def render(result: PlanResult, top: int = 8, show_grid: bool = True) -> str:
    spec = result.spec
    cur = spec.currency
    L: list[str] = []
    L.append(f"# {spec.name}")
    L.append("")
    L.append(f"{result.skeletons} date/order combinations checked across "
             f"{len(result.options)} leg searches. Scored as cash + "
             f"{money(spec.value_of_time, cur)}/hour of door-to-door travel time"
             + (f" − {money(spec.hotel_night, cur)} per night on a night train "
                "(half that on a night coach)" if spec.hotel_night else "") + ".")
    L.append("")
    L.append("Prices: plain = live fare, `~` = guide price for train/coach "
             "(typical, check operator), `*` = your own quote. "
             f"Per {'person' if spec.travellers == 1 else f'{spec.travellers} people'}"
             ", fares + airport/station transfers.")
    for note in spec.notes:
        L.append("")
        L.append(f"> {note}")
    L.append("")

    if not result.itineraries:
        L.append("**No complete itinerary could be priced.**")
        L.append("")
        if result.unpriced:
            L.append("Legs with no options found:")
            for o, d, dd in result.unpriced[:30]:
                L.append(f"- {day(dd)} {o} → {d}")
        L.extend(_warnings(result))
        return "\n".join(L)

    best = result.itineraries[0]
    cheapest = result.cheapest[0]

    L.append("## Best overall")
    L.append("")
    L.extend(_itinerary_detail(best, cur))
    if cheapest is not best:
        L.append("")
        L.append(f"## Cheapest cash total ({money(cheapest.cash, cur)}, "
                 f"{money(best.cash - cheapest.cash, cur)} less than best overall)")
        L.append("")
        L.extend(_itinerary_detail(cheapest, cur))

    L.append("")
    L.append(f"## Top {min(top, len(result.itineraries))} itineraries")
    L.append("")
    L.append("| # | Cash | Travel time | Stays | Route |")
    L.append("|---|---|---|---|---|")
    for n, it in enumerate(result.itineraries[:top], 1):
        L.append(f"| {n} | {money(it.cash, cur)} | {hours(it.door_minutes)} | "
                 f"{_stay_line(it)} | {_route_line(it, cur)} |")

    if show_grid:
        L.append("")
        L.extend(_grid(result, cur))
    L.extend(_warnings(result))
    return "\n".join(L)


def _itinerary_detail(it: Itinerary, cur: str) -> list[str]:
    L = [f"**{money(it.cash, cur)}** total · {hours(it.door_minutes)} door-to-door travel · "
         f"stays: {_stay_line(it)}"]
    if it.extra_cost or it.extra_minutes:
        L[0] += f" · includes {money(it.extra_cost, cur)} to get to/from your start city"
    L.append("")
    L.append("| Date | Leg | How | Times | Fare | Door to door | Book |")
    L.append("|---|---|---|---|---|---|---|")
    for leg in it.legs:
        c = leg.choice
        o = c.option
        how = f"{MODE_LABEL[o.mode]} · {o.carrier}" + (f" ({o.stops} stop)" if o.stops else "")
        L.append(f"| {day(leg.depart_date)} | {leg.origin.name} → {leg.destination.name} "
                 f"({o.origin.code}→{o.destination.code}) | {how} | {_times(c)} | "
                 f"{_price(c, cur)} | {hours(c.door_minutes)} | "
                 f"{f'[link]({o.booking_url})' if o.booking_url else ''} |")
    L.append("")
    for leg in it.legs:
        c = leg.choice
        o = c.option
        lines = []
        if o.notes:
            lines.append(f"  - note: {o.notes}")
        if o.price_kind == GUIDE and o.typical_price:
            from_fare = c.fare * o.price / o.typical_price
            lines.append(f"  - guide price: from {money(from_fare, cur)}, typically {money(c.fare, cur)}")
        for alt in leg.alternatives[:3]:
            a = alt.option
            lines.append(f"  - vs {MODE_LABEL[a.mode]} ({a.carrier}, {a.origin.code}→{a.destination.code}"
                         f"{', ' + _times(alt) if _times(alt) else ''}): {_price(alt, cur)}, "
                         f"{hours(alt.door_minutes)} door to door: {_verdict(c, alt, cur)}")
        if lines:
            L.append(f"- **{day(leg.depart_date)} {leg.origin.name} → {leg.destination.name}**")
            L.extend(lines)
    return L


def _grid(result: PlanResult, cur: str) -> list[str]:
    grid = date_grid(result)
    if not grid:
        return []
    all_days = sorted({d for row in grid.values() for d in row})
    L = ["## Cheapest fare by date", "",
         "Cheapest cash option on each route and day (any mode). Blank = not needed for any "
         "valid itinerary or nothing found.", ""]
    L.append("| Route | " + " | ".join(day(d) for d in all_days) + " |")
    L.append("|---|" + "---|" * len(all_days))
    names = result.places
    for (o, d), row in sorted(grid.items(), key=lambda kv: (min(kv[1]), kv[0])):
        lo = min(c.cash for c in row.values())
        cells = []
        for dd in all_days:
            c = row.get(dd)
            if c is None:
                cells.append("")
                continue
            text = (f"{KIND_MARK[c.option.price_kind]}{money(c.cash, cur)} "
                    f"{'✈' if c.option.mode == FLIGHT else MODE_LABEL[c.option.mode]}")
            varies = len({round(x.cash) for x in row.values()}) > 1
            cells.append(f"**{text}**" if varies and c.cash == lo else text)
        L.append(f"| {names[o].name} → {names[d].name} | " + " | ".join(cells) + " |")
    return L


def _warnings(result: PlanResult) -> list[str]:
    if not result.warnings and not result.unpriced:
        return []
    L = ["", "## Warnings", ""]
    seen = set()
    for w in result.warnings:
        if w not in seen:
            seen.add(w)
            L.append(f"- {w}")
        if len(seen) >= 15:
            L.append(f"- … and {len(set(result.warnings)) - 15} more")
            break
    if result.unpriced:
        legs = ", ".join(f"{o}→{d} {dd:%d %b}" for o, d, dd in result.unpriced[:12])
        L.append(f"- No options found for: {legs}" + (" …" if len(result.unpriced) > 12 else ""))
    return L
