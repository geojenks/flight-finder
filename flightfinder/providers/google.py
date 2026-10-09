"""Live fares for every airline via Google Flights, using `fast-flights`.

Install with ``pip install 'flightfinder[google]'``. Google may throttle
heavy use, so results are cached and requests are spaced out.
"""

from __future__ import annotations

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
                 use_cache: bool = True, min_interval: float = 1.5, keep: int = 5):
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
        from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights

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
            try:
                result = get_flights(query)
            except FlightsNotFound:
                result = []
            except Exception as exc:
                self.errors.append(f"{o}->{d} {day}: {exc}")
                return []
            finally:
                self._last = time.monotonic()

        rows = []
        for f in result:
            segs = f.flights
            if not segs or not f.price:
                continue
            first, last = segs[0], segs[-1]
            dep_date = date(*first.departure.date)
            arr_date = date(*last.arrival.date)
            rows.append({
                "price": f.price,
                "airlines": list(f.airlines),
                "from": first.from_airport.code,
                "to": last.to_airport.code,
                "dep": "%02d:%02d" % first.departure.time,
                "arr": "%02d:%02d" % last.arrival.time,
                "offset": (arr_date - dep_date).days,
                "elapsed": _elapsed(first, last),
                "air": sum(s.duration or 0 for s in segs),
                "stops": len(segs) - 1,
            })
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


def _elapsed(first, last) -> int:
    """Departure-to-arrival minutes on the local clocks (0 if unknown)."""
    try:
        dep = datetime(*first.departure.date, *first.departure.time)
        arr = datetime(*last.arrival.date, *last.arrival.time)
        return int((arr - dep).total_seconds() // 60)
    except (TypeError, ValueError):
        return 0
