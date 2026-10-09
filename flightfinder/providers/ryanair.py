"""Live Ryanair fares from the public fare-finder API (no key needed).

One request returns the cheapest fare for every day of a month on a route,
which is exactly what "which date is cheapest?" needs.
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from urllib.parse import urlencode

from ..models import FLIGHT, LIVE, Option, Place, Terminal, clock_shift_minutes
from .base import DiskCache, Provider, ProviderError, get_json

API = "https://www.ryanair.com/api/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay"


class RyanairProvider(Provider):
    name = "ryanair"
    modes = (FLIGHT,)

    def __init__(self, currency: str = "GBP", use_cache: bool = True):
        self.currency = currency
        self.cache = DiskCache("ryanair", enabled=use_cache)
        self._lock = threading.Lock()
        self._month: dict[tuple[str, str, str], dict] = {}
        self.errors: list[str] = []

    def _month_fares(self, o: str, d: str, month: date) -> dict:
        key = (o, d, month.strftime("%Y-%m"))
        with self._lock:
            if key in self._month:
                return self._month[key]
        cache_key = "|".join(key) + "|" + self.currency
        data = self.cache.get(cache_key)
        if data is None:
            url = API.format(o=o, d=d) + "?" + urlencode({
                "outboundMonthOfDate": month.replace(day=1).isoformat(),
                "currency": self.currency,
            })
            try:
                data = get_json(url)
                self.cache.put(cache_key, data)
            except ProviderError as exc:
                # Routes Ryanair doesn't fly come back as 404s; that's normal
                # and worth remembering. Anything else may be transient.
                data = {}
                if "404" in str(exc):
                    self.cache.put(cache_key, data)
                else:
                    self.errors.append(str(exc))
        by_day = {}
        for fare in (data.get("outbound") or {}).get("fares") or []:
            if fare.get("unavailable") or fare.get("soldOut") or not fare.get("price"):
                continue
            by_day[fare.get("day")] = fare
        with self._lock:
            self._month[key] = by_day
        return by_day

    def _option(self, o: Terminal, d: Terminal, day: date, fare: dict,
                shift: int) -> Option | None:
        price = fare["price"]
        dep = _dt(fare.get("departureDate"))
        arr = _dt(fare.get("arrivalDate"))
        # Both times are local clock times, so take off the time-zone shift.
        duration = int((arr - dep).total_seconds() // 60) - shift if dep and arr else 0
        if duration <= 0 or duration > 12 * 60:
            duration = 150
        return Option(
            mode=FLIGHT,
            carrier="Ryanair",
            origin=o,
            destination=d,
            depart_date=day,
            price=float(price.get("value")),
            currency=price.get("currencyCode", self.currency),
            duration_minutes=duration,
            source=self.name,
            price_kind=LIVE,
            depart_time=dep.strftime("%H:%M") if dep else None,
            arrive_time=arr.strftime("%H:%M") if arr else None,
            arrive_day_offset=(arr.date() - dep.date()).days if dep and arr else 0,
            booking_url=(
                "https://www.ryanair.com/gb/en/trip/flights/select?"
                + urlencode({"adults": 1, "dateOut": day.isoformat(),
                             "originIata": o.code, "destinationIata": d.code,
                             "isReturn": "false"})
            ),
            notes="Basic fare: cabin bag and seat cost extra.",
        )

    def search(self, origin: Place, destination: Place, day: date) -> list[Option]:
        out = []
        shift = clock_shift_minutes(origin.country, destination.country)
        for o in origin.airports:
            for d in destination.airports:
                fare = self._month_fares(o.code, d.code, day).get(day.isoformat())
                if fare:
                    opt = self._option(o, d, day, fare, shift)
                    if opt:
                        out.append(opt)
        return out


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
