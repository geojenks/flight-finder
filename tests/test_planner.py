from datetime import date

import pytest

from flightfinder.models import FLIGHT, LIVE, Option
from flightfinder.places import load_places
from flightfinder.planner import plan, skeletons
from flightfinder.providers import GroundProvider, ManualProvider
from flightfinder.providers.base import Provider
from flightfinder.report import render
from flightfinder.tripspec import load_trip, parse_dates, parse_nights, trip_from_dict

PLACES = load_places()
TRIP = "trips/2026-10-berlin-amsterdam-barcelona.yaml"


class FakeFlights(Provider):
    """Deterministic flights so the planner can be tested offline."""

    name = "fake"
    modes = (FLIGHT,)

    def __init__(self, prices):
        self.prices = prices  # {(origin_code, dest_code, date): price}

    def search(self, origin, destination, day):
        out = []
        for o in origin.airports:
            for d in destination.airports:
                p = self.prices.get((o.code, d.code, day))
                if p is not None:
                    out.append(Option(FLIGHT, "FakeAir", o, d, day, p, "GBP", 110, self.name,
                                      LIVE, "09:00", "11:50"))
        return out


def test_parse_dates_and_nights():
    assert parse_dates("2026-10-26..2026-10-28") == (
        date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28))
    assert parse_dates(["2026-10-27", date(2026, 10, 26)]) == (
        date(2026, 10, 26), date(2026, 10, 27))
    assert parse_nights("1-3") == (1, 3)
    assert parse_nights(2) == (2, 2)
    with pytest.raises(ValueError):
        parse_nights("3-1")


def test_restricted_place_keeps_ground_terminals():
    spec = trip_from_dict({
        "start": {"from": ["london[STN]"], "depart": "2026-10-26"},
        "stops": [{"place": "berlin", "nights": 2}],
    }, PLACES)
    london = spec.starts[0].place
    assert [t.code for t in london.airports] == ["STN"]
    assert london.terminal("STP") is not None


def test_orderings_respect_groups():
    spec = load_trip(TRIP, PLACES)
    orders = [[s.place.key for s in o] for o in spec.orderings()]
    assert sorted(orders) == [["amsterdam", "berlin", "barcelona"],
                              ["berlin", "amsterdam", "barcelona"]]


def test_skeletons_hit_date_windows():
    spec = load_trip(TRIP, PLACES)
    skels = skeletons(spec)
    assert skels
    for legs in skels:
        assert legs[0].depart in spec.start_dates
        assert legs[-1].depart in spec.end_dates
        # Barcelona leg departs on/just before the 30th/31st
        assert legs[2].depart in {date(2026, 10, d) for d in (29, 30, 31)}


def test_plan_prefers_cheap_day_and_reports_train_alternative():
    spec = load_trip(TRIP, PLACES)
    spec.hotel_night = 0  # keep night coaches from winning on hotel savings
    d = lambda n, m=10: date(2026, m, n)  # noqa: E731
    prices = {}
    for day in (d(26), d(27)):
        prices[("BRS", "AMS", day)] = 90
        prices[("BRS", "BER", day)] = 95
    prices[("BRS", "AMS", d(26))] = 40  # cheap Monday into Amsterdam
    for day in (d(27), d(28), d(29)):
        prices[("AMS", "BER", day)] = 120  # expensive hop: ground should win
        prices[("BER", "AMS", day)] = 120
    for day in (d(29), d(30), d(31)):
        prices[("BER", "BCN", day)] = 50
        prices[("AMS", "BCN", day)] = 70
    for day in (d(5, 11), d(6, 11)):
        prices[("BCN", "BRS", day)] = 45
    result = plan(spec, [FakeFlights(prices), GroundProvider()], workers=2)
    best = result.itineraries[0]
    legs = [(lp.origin.key, lp.destination.key, lp.choice.option.mode) for lp in best.legs]
    assert legs[0][:2] == ("bristol", "amsterdam")
    assert best.legs[0].depart_date == d(26)
    assert legs[1][2] != FLIGHT  # train/coach beats a £120 flight
    assert legs[2][:2] == ("berlin", "barcelona")
    text = render(result)
    assert "Best overall" in text and "Cheapest fare by date" in text


def test_night_train_only_on_its_days():
    ground = GroundProvider()
    ams, ber = PLACES["amsterdam"], PLACES["berlin"]
    wed = [o for o in ground.search(ams, ber, date(2026, 10, 28)) if o.mode == "night-train"]
    tue = [o for o in ground.search(ams, ber, date(2026, 10, 27)) if o.mode == "night-train"]
    assert wed and wed[0].overnight and wed[0].arrive_date == date(2026, 10, 29)
    assert not tue


def test_manual_quotes():
    q = ManualProvider(quotes=[{"from": "BRS", "to": "BER", "date": "2026-10-26",
                                "price": 46, "carrier": "easyJet", "duration": "1h55"}])
    opts = q.search(PLACES["bristol"], PLACES["berlin"], date(2026, 10, 26))
    assert len(opts) == 1 and opts[0].duration_minutes == 115 and opts[0].price_kind == "manual"


def test_overnight_arrival_counts_stay_from_arrival_day():
    """A night coach arriving the next morning must still leave the stop's minimum nights."""
    spec = load_trip(TRIP, PLACES)
    result = plan(spec, [GroundProvider(), FakeFlights({
        ("BER", "BCN", date(2026, 10, 30)): 50, ("AMS", "BCN", date(2026, 10, 30)): 50,
        ("BER", "BCN", date(2026, 10, 31)): 50, ("AMS", "BCN", date(2026, 10, 31)): 50,
        ("BCN", "BRS", date(2026, 11, 5)): 45, ("BCN", "BRS", date(2026, 11, 6)): 45,
    })], workers=2)
    assert result.itineraries
    for it in result.itineraries:
        for prev, nxt in zip(it.legs, it.legs[1:]):
            assert (nxt.depart_date - prev.choice.option.arrive_date).days >= 1


def test_ryanair_parses_cheapest_per_day(monkeypatch, tmp_path):
    from flightfinder.providers import ryanair

    monkeypatch.setenv("FLIGHTFINDER_CACHE", str(tmp_path))
    payload = {"outbound": {"fares": [
        {"day": "2026-10-26", "departureDate": "2026-10-26T06:30:00",
         "arrivalDate": "2026-10-26T09:25:00", "price": {"value": 29.99, "currencyCode": "GBP"},
         "soldOut": False, "unavailable": False},
        {"day": "2026-10-27", "price": None, "unavailable": True},
    ]}}
    calls = []

    def fake_get_json(url, timeout=20.0):
        calls.append(url)
        return payload

    monkeypatch.setattr(ryanair, "get_json", fake_get_json)
    london = trip_from_dict({"start": {"from": ["london[STN]"], "depart": "2026-10-26"},
                             "stops": [{"place": "berlin"}]}, PLACES).starts[0].place
    prov = ryanair.RyanairProvider("GBP")
    [opt] = prov.search(london, PLACES["berlin"], date(2026, 10, 26))
    assert opt.price == 29.99 and opt.depart_time == "06:30"
    assert opt.duration_minutes == 115  # 2h55 on the clocks minus the 1h time difference
    assert prov.search(london, PLACES["berlin"], date(2026, 10, 27)) == []
    assert len(calls) == 1  # one request per route per month
