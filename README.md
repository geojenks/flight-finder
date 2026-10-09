# flightfinder

Plan multi-stop European trips with **rough dates**: give it the places, the
order (or "any order"), how long you want in each, and date windows. It
searches every valid combination of visiting order and dates, prices each leg
by **flight, train, night train and coach**, and tells you:

- the best overall itinerary (cash + the value of your time), and the cheapest;
- for every leg, how the other modes compare door to door ("train is £12 more
  but 1h40 faster, city centre to city centre");
- a **cheapest-by-date grid** for every route, so you can see which day to move.

## Install

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[google]'      # drop [google] to skip Google Flights
```

## Use

```sh
# Plan a whole trip
flightfinder search trips/2026-10-berlin-amsterdam-barcelona.yaml

# Save the report, or ignore travel time and just find the cheapest
flightfinder search trips/my-trip.yaml --out out/my-trip.md --value-of-time 0

# One leg over a range of dates: which day is cheapest, and how?
flightfinder dates bristol berlin 2026-10-24..2026-10-30

# Only search some London airports
flightfinder dates 'london[STN,LGW]' amsterdam 2026-10-26..2026-10-27

flightfinder places     # known cities, airports and stations
```

Common options (after the command): `--providers ryanair,google,ground`,
`--quotes quotes.yaml` (your own prices), `--places` / `--routes` (extra data),
`--no-cache`.

## Trip files

```yaml
name: Berlin, Amsterdam & Barcelona
currency: GBP
value_of_time: 10        # £ per hour door to door; 0 = purely cheapest
hotel_night: 70          # credited when you sleep on a night train (half for a coach)

start:
  from: [bristol, london]                 # or {place: london, extra_cost: 20, extra_minutes: 150}
  depart: [2026-10-26, 2026-10-27]        # list, single date, or 2026-10-25..2026-10-28
stops:
  - {place: berlin, nights: 1-3, group: north}     # same group = either order
  - {place: amsterdam, nights: 1-3, group: north}
  - {place: barcelona, arrive: [2026-10-30, 2026-10-31]}
end:
  to: [bristol, london]                   # defaults to start.from
  depart: [2026-11-05, 2026-11-06]
```

Stops take `nights` (a number or range, counted from the day you actually
arrive, so a night train that arrives next morning is handled), `arrive` and/or
`depart` date windows, and `group`. Use `london[LGW,STN]` to limit airports.
Other keys: `travellers`, `modes`, `max_stops` (flight connections), `fx`
(exchange rates into your currency), `notes`.

## How legs are compared

Each option is costed **door to door**: city centre → terminal (time and
fare, from `places.yaml`) + time at the terminal (2h30 for flights, 75 min for
Eurostar, ~20 min for other trains/coaches) + the journey + terminal → city
centre. The ranking score is

    fares + transfers + value_of_time × hours − hotel night saved by sleeping on board

so a £30 flight from Stansted can lose to a £45 train that leaves from the
centre.

## Where prices come from

| Source | What | Notes |
|---|---|---|
| `ryanair` | Live Ryanair fares, cheapest per day | Public fare-finder API, no key. Basic fare (no bag/seat). |
| `google` | Live fares for all airlines (easyJet, KLM, BA, Vueling…) via Google Flights | Uses [fast-flights](https://github.com/AWeirdDev/flights), which scrapes Google; may break or be throttled. Results cached 6h. |
| `ground` | Trains, night trains, coaches | **Guide prices** from `flightfinder/data/ground_routes.yaml` (marked `~`). There's no free open API for European rail/coach fares, so check the operator (linked) before booking. Includes schedules (e.g. night-train days) and engineering-works periods. |
| `--quotes` | Anything you found yourself | Marked `*`. |

Fares are cached in `~/.cache/flightfinder` (`FLIGHTFINDER_CACHE` to move it).

## Development

```sh
pip install -e '.[dev]' && pytest
```

Ideas next: live rail fares (DB / Trainline / SNCF), FlixBus live prices,
baggage-inclusive pricing, a small web UI, price-drop alerts.
