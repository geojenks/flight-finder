"""Live fares for every airline via Google Flights, using `fast-flights`.

Install with ``pip install 'flightfinder[google]'``. Google may throttle
heavy use, so results are cached and requests are spaced out.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from urllib.parse import quote

from ..models import FLIGHT, LIVE, Option, Place, Terminal, clock_shift_minutes
from .base import DiskCache, Provider, ProviderError


class GoogleFlightsProvider(Provider):
    name = "google"
    modes = (FLIGHT,)

    def __init__(self, currency: str = "GBP", max_stops: int = 1,
                 use_cache: bool = True, min_interval: float = 1.5, keep: int = 8):
        try:
            import fast_flights  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "Google Flights needs fast-flights: pip install 'flightfinder[google]'"
            ) from exc
        self.currency = currency
        self.max_stops = max_stops
        self.cache = DiskCache("google", enabled=use_cache)
        self.min_interval = min_interval
        self.keep = keep
        self._lock = threading.Lock()
        self._last = 0.0
        self.errors: list[str] = []

    def _fetch(self, o: str, d: str, day: date) -> list[dict]:
        key = f"{o}|{d}|{day}|{self.currency}|{self.max_stops}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html

        with self._lock:  # be polite: one request at a time, spaced out
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            query = create_query(
                flights=[FlightQuery(date=day.isoformat(), from_airport=o, to_airport=d,
                                     max_stops=self.max_stops)],
                seat="economy",
                trip="one-way",
                passengers=Passengers(adults=1),
                language="en-GB",
                currency=self.currency,
            )
            rows = None
            for attempt in range(2):  # Google sometimes serves an empty page; retry once
                try:
                    rows = parse_results(fetch_flights_html(query))
                    break
                except Exception as exc:
                    if attempt:
                        self.errors.append(f"{o}->{d} {day}: {exc}")
                    else:
                        time.sleep(3 * self.min_interval)
                finally:
                    self._last = time.monotonic()
            if rows is None:
                return []

        rows = [r for r in rows if r["from"] == o and r["to"] == d]
        rows.sort(key=lambda r: r["price"])
        rows = rows[: self.keep]
        self.cache.put(key, rows)
        return rows

    def search(self, origin: Place, destination: Place, day: date) -> list[Option]:
        out = []
        shift = clock_shift_minutes(origin.country, destination.country)
        for o in origin.airports:
            for d in destination.airports:
                for row in self._fetch(o.code, d.code, day):
                    out.append(self._option(o, d, day, row, shift))
        return out

    def _option(self, o: Terminal, d: Terminal, day: date, row: dict, shift: int) -> Option:
        carrier = ", ".join(row["airlines"]) or "?"
        url = ("https://www.google.com/travel/flights?q="
               + quote(f"one way flights from {o.code} to {d.code} on {day.isoformat()}"))
        return Option(
            mode=FLIGHT,
            carrier=carrier,
            origin=o,
            destination=d,
            depart_date=day,
            price=float(row["price"]),
            currency=self.currency,
            # Elapsed local clock time minus the time-zone shift includes any
            # layover; never report less than the time actually in the air.
            duration_minutes=max(int(row["elapsed"]) - shift, int(row["air"])),
            source=self.name,
            price_kind=LIVE,
            depart_time=row["dep"],
            arrive_time=row["arr"],
            arrive_day_offset=int(row["offset"]),
            stops=int(row["stops"]),
            booking_url=url,
        )


def _time(value) -> tuple[int, int]:
    """Google drops zero components: [8] is 08:00, [None, 31] is 00:31."""
    padded = [*(value or []), None, None]
    return (padded[0] or 0, padded[1] or 0)


def parse_results(html: str) -> list[dict]:
    """Read flights out of a Google Flights results page.

    fast-flights' own parser only reads the "other flights" list and crashes on
    unpriced entries, so this reads both the "best" (section 2) and "other"
    (section 3) lists and skips anything without a price.
    """
    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("no results data in page (blocked or consent page?)")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []
    payload = json.loads(data)

    rows = []
    for section in (2, 3):
        try:
            entries = payload[section][0] or []
        except (IndexError, TypeError):
            continue
        for k in entries:
            try:
                flight, price = k[0], k[1][0][1]
                segs = flight[2]
            except (IndexError, TypeError):
                continue
            if not price or not segs:
                continue
            first, last = segs[0], segs[-1]
            dep = datetime(*first[20], *_time(first[8]))
            arr = datetime(*last[21], *_time(last[10]))
            rows.append({
                "price": price,
                "airlines": list(flight[1] or []),
                "from": first[3],
                "to": last[6],
                "dep": dep.strftime("%H:%M"),
                "arr": arr.strftime("%H:%M"),
                "offset": (arr.date() - dep.date()).days,
                "elapsed": int((arr - dep).total_seconds() // 60),
                "air": sum(s[11] or 0 for s in segs),
                "stops": len(segs) - 1,
            })
    return rows
