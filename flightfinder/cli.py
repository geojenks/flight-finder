"""Command line: `flightfinder search trips/my-trip.yaml`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .places import load_places
from .planner import cost_option, plan
from .providers import GroundProvider, ManualProvider, ProviderError, RyanairProvider
from .providers.ground import load_routes
from .report import KIND_MARK, MODE_LABEL, day, hours, money, render
from .tripspec import load_trip, parse_dates, parse_place, trip_from_dict

ALL_PROVIDERS = ("ryanair", "google", "ground")


def build_providers(names, currency, args, max_stops=1):
    providers, notes = [], []
    for name in names:
        if name == "ryanair":
            providers.append(RyanairProvider(currency, use_cache=not args.no_cache))
        elif name == "google":
            try:
                from .providers.google import GoogleFlightsProvider
                providers.append(GoogleFlightsProvider(currency, max_stops=max_stops,
                                                       use_cache=not args.no_cache))
            except ProviderError as exc:
                notes.append(f"Google Flights skipped: {exc}")
        elif name == "ground":
            providers.append(GroundProvider(load_routes(args.routes)))
        else:
            raise SystemExit(f"Unknown provider {name!r}; choose from {', '.join(ALL_PROVIDERS)}")
    if args.quotes:
        providers.append(ManualProvider(args.quotes))
    return providers, notes


def _progress(n, total):
    if sys.stderr.isatty():
        print(f"\rSearching legs {n}/{total}…", end="" if n < total else "\n",
              file=sys.stderr, flush=True)


def cmd_search(args) -> int:
    places = load_places(args.places)
    spec = load_trip(args.trip, places)
    if args.value_of_time is not None:
        spec.value_of_time = args.value_of_time
    names = args.providers.split(",") if args.providers else list(ALL_PROVIDERS)
    providers, notes = build_providers(names, spec.currency, args, spec.max_stops)
    result = plan(spec, providers, progress=_progress)
    for prov in providers:
        errors = getattr(prov, "errors", [])
        if len(errors) > 3:
            result.warnings.append(f"{prov.name}: {len(errors)} requests failed, e.g. {errors[0]}")
        else:
            result.warnings.extend(f"{prov.name}: {e}" for e in errors)
    result.warnings[:0] = notes
    text = render(result, top=args.top, show_grid=not args.no_grid)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", "utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(text)
    return 0 if result.itineraries else 1


def cmd_dates(args) -> int:
    """Price one leg across a range of dates: which day is cheapest?"""
    places = load_places(args.places)
    origin = parse_place(args.origin, places)
    dest = parse_place(args.destination, places)
    days = parse_dates(args.dates)
    spec = trip_from_dict({
        "currency": args.currency, "start": {"from": [args.origin], "depart": args.dates},
        "stops": [{"place": args.destination}],
    }, places)
    names = args.providers.split(",") if args.providers else list(ALL_PROVIDERS)
    providers, notes = build_providers(names, spec.currency, args)
    for n in notes:
        print(n, file=sys.stderr)
    cur = spec.currency
    print(f"| Date | Cheapest | How | Times | Door to door | Fastest door to door |")
    print("|---|---|---|---|---|---|")
    for dd in days:
        found = []
        for prov in providers:
            try:
                found.extend(prov.search(origin, dest, dd))
            except Exception as exc:
                print(f"{prov.name}: {exc}", file=sys.stderr)
        costed = [c for c in (cost_option(o, spec) for o in found) if c]
        if not costed:
            print(f"| {day(dd)} | – | | | | |")
            continue
        cheap = min(costed, key=lambda c: c.cash)
        fast = min(costed, key=lambda c: c.door_minutes)
        o = cheap.option
        times = f"{o.depart_time}–{o.arrive_time}" if o.depart_time else ""
        print(f"| {day(dd)} | {KIND_MARK[o.price_kind]}{money(cheap.cash, cur)} | "
              f"{MODE_LABEL[o.mode]} {o.carrier} {o.origin.code}→{o.destination.code} | {times} | "
              f"{hours(cheap.door_minutes)} | {MODE_LABEL[fast.option.mode]} "
              f"{fast.option.carrier} {hours(fast.door_minutes)}, "
              f"{KIND_MARK[fast.option.price_kind]}{money(fast.cash, cur)} |")
    for prov in providers:
        for e in getattr(prov, "errors", []):
            print(f"{prov.name}: {e}", file=sys.stderr)
    return 0


def cmd_places(args) -> int:
    for key, place in sorted(load_places(args.places).items()):
        codes = ", ".join(f"{t.code} ({t.kind})" for t in place.terminals)
        print(f"{key:12} {place.name:12} {codes}")
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--places", type=Path, help="extra places YAML")
    common.add_argument("--routes", type=Path, help="extra train/coach routes YAML")
    common.add_argument("--quotes", type=Path, help="your own price quotes YAML")
    common.add_argument("--providers", help=f"comma list (default: {','.join(ALL_PROVIDERS)})")
    common.add_argument("--no-cache", action="store_true", help="ignore cached fares")

    ap = argparse.ArgumentParser(prog="flightfinder", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", parents=[common], help="plan a multi-stop trip from a trip file")
    s.add_argument("trip", type=Path)
    s.add_argument("--top", type=int, default=8)
    s.add_argument("--out", type=Path, help="also write the Markdown report here")
    s.add_argument("--no-grid", action="store_true", help="skip the date grid")
    s.add_argument("--value-of-time", type=float,
                   help="override value of an hour of travel (0 = pure cheapest)")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("dates", parents=[common], help="cheapest way between two places for each date")
    d.add_argument("origin")
    d.add_argument("destination")
    d.add_argument("dates", help="e.g. 2026-10-25..2026-10-31")
    d.add_argument("--currency", default="GBP")
    d.set_defaults(func=cmd_dates)

    p = sub.add_parser("places", parents=[common], help="list known places and their terminals")
    p.set_defaults(func=cmd_places)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
