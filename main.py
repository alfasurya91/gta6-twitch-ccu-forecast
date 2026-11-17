#!/usr/bin/env python3
"""GTA VI launch-day Twitch concurrent-viewers forecaster.

Entry for the make.fun bounty gta6-twitch-ccu-forecast-01: for each of hours
2..24 of a game's launch day, forecast five quantiles (5%, 25%, 50%, 75%, 95%)
of that hour's concurrent Twitch viewers.

How it works, in plain words
----------------------------
The house baseline forecasts every hour equal to hour 1, with a spread that
widens like a random walk. That ignores the one thing every real launch day
has: a shape. Viewership dips in the small hours, then ramps as regions
unlock (console launches unlock at local midnight, so 00:00 UTC is only the
UK unlock and the US unlocks hours later) and as the day reaches the evening
peak, then decays overnight. This program learns that shape from the
"comparables" shipped in each case: the full hourly viewer curves of about
38 past big game launches.

For each comparable launch it forms, for every forecast hour h, the log-ratio
ln(viewers in hour h / viewers in hour 1). It pools those log-ratios across
comparables and forecasts the median as

    hour-1 print * exp(median log-ratio for hour h)

with the outer quantiles taken from the spread of the log-ratios at that
hour, assuming the log-ratios are roughly normal (median +/- z * sigma,
sigma estimated from the interquartile range, shrunk by a factor 0.6 because
the raw interquartile range overstates how far a typical launch sits from
its group median).

Three guards keep one weird launch from wrecking the forecast:

1.  Tier filter. Comparables carry a "Tier:" tag in their unlock notes.
    Early-access-contaminated launches (their peak happened days before
    release day, so their release-day curve is flat or falling) and "weak"
    launches are dropped, as is the target game's own pre-release history
    (it is not a launch). Only clean "comparable" launches are used.

2.  Dead-hour-1 split. Hour 1 does not mean the same thing for every
    launch. If a launch's hour-1 print is already close to its pre-release
    evening peak, the audience was there before the day began and the day's
    ramp is small. If hour 1 is far below that evening peak, hour 1 was a
    dead hour and the main unlock wave is still coming, so the ramp is big.
    The program measures x = ln(hour-1 / evening peak), where the evening
    peak is the largest hourly viewership in the 24 hours before release
    day, and splits the comparables in two at x = ln(0.5): hour 1 below
    half the evening peak means the wave is still ahead. The target's own
    x decides which half it is forecast from. (On the four practice cases
    this split is the difference between a 0.33 and a 0.18 mean pinball
    loss.)

3.  Clamping. Log-ratios are clamped to [-2, +3] (a launch can at most
    fall to ~1/7th or rise to ~20x its hour-1 print). Some tiny-category
    launches ramped 100x-2000x from a near-dead hour 1; without the clamp
    they would drag the whole distribution up. Missing or sparse hours in
    a comparable are simply skipped.

If the target has no usable pre-release history, the split cannot be made
and the program falls back to pooling all clean comparables. If the hour-1
print itself is missing, it anchors on the evening peak instead. If a case
has no usable comparables at all, it falls back to flat persistence with a
widening spread (the honest no-data forecast).

The program is deterministic (the median and quartiles do not depend on
input order), uses only the Python standard library, makes no network
calls, and runs in well under a second per case.
"""

import json
import math
import os
import re
from datetime import datetime, timedelta

# Normal quantiles for the 5%, 25%, 50%, 75%, 95% levels.
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z_SCORES = (-1.645, -0.674, 0.0, 0.674, 1.645)

# Log-ratio clamp: a launch may fall to ~1/7th or rise to ~20x its hour-1
# print; anything beyond that is a near-dead category waking up, not a
# launch-day shape, and must not dominate the pooled distribution.
CLAMP_LO = -2.0
CLAMP_HI = 3.0

# Split threshold on x = ln(hour-1 / evening peak): ln(0.5). Hour 1 below
# half the pre-release evening peak means the main unlock wave is ahead.
SPLIT_X = math.log(0.5)

# Spread shrink: the raw interquartile range of the pooled log-ratios
# overstates how far a typical launch sits from its group median, because
# it mixes launches with different unlock structures. 0.6 keeps the 90%
# band roughly honest (about 0.86 coverage on the four practice cases)
# while scoring clearly better than the unshrunk spread.
SPREAD_SHRINK = 0.6

# Tiers (parsed from the "Tier:" tag in a comparable's unlock notes) that
# must not be used as launch-shape evidence.
EXCLUDED_TIERS = {"early-access-contaminated", "weak", "target-history"}


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def quantile(values, q):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def tier_of(comparable):
    match = re.search(r"Tier:\s*([^.;|]+)", comparable.get("unlock", ""))
    return match.group(1).strip() if match else ""


def release_day_curve(comparable):
    """Return (hour-1 viewers, {clock hour: viewers on release day},
    evening peak) for one comparable, or None if hour 1 is missing.

    Clock hour 0 is 00:00-01:00 UTC on release day; the evening peak is
    the largest hourly viewership in the 24 hours before release day.
    """
    day_start = parse_time(comparable["day_start"])
    by_hour = {}
    evening_peak = None
    for point in comparable.get("hourly", []):
        viewers = point.get("viewers")
        if viewers is None or viewers <= 0:
            continue
        when = parse_time(point["from"])
        if day_start <= when < day_start + timedelta(days=1):
            by_hour[when.hour] = viewers
        elif day_start - timedelta(days=1) <= when < day_start:
            evening_peak = viewers if evening_peak is None else max(evening_peak, viewers)
    hour_one = by_hour.get(0)
    if hour_one is None:
        return None
    return hour_one, by_hour, evening_peak


def target_evening_peak(case):
    """Largest hourly viewership in the 24 hours before the target's
    release day, from the case's own pre-release history."""
    day_start = parse_time(case["day_start"])
    peak = None
    for row in case.get("history_hourly", []):
        viewers = row.get("viewers")
        if viewers is None or viewers <= 0:
            continue
        when = parse_time(row["from"])
        if day_start - timedelta(days=1) <= when < day_start:
            peak = viewers if peak is None else max(peak, viewers)
    if peak is None:
        # Sparse history: fall back to any pre-release point at all.
        for row in case.get("history_hourly", []):
            viewers = row.get("viewers")
            if viewers is None or viewers <= 0:
                continue
            if parse_time(row["from"]) < day_start:
                peak = viewers if peak is None else max(peak, viewers)
    return peak


def hour_one_print(case):
    for row in case.get("known", []):
        if row.get("hour") == 1 and row.get("viewers"):
            return row["viewers"]
    return None


def pooled_stats(values):
    """(median, sigma) of a list of log-ratios; sigma from the
    interquartile range, floored so the band never collapses."""
    med = median(values)
    q25 = quantile(values, 0.25)
    q75 = quantile(values, 0.75)
    sigma = max(0.05, (q75 - q25) / 1.349) if q25 is not None else 0.3
    return med, sigma


def forecast_case(case):
    forecast_hours = [row["hour"] for row in case.get("forecast", [])]
    hour_one = hour_one_print(case)
    evening_peak = target_evening_peak(case)

    # --- Collect clean comparables: per-launch log-ratios and x. ---
    launches = []
    for comparable in case.get("comparables", []):
        if tier_of(comparable) in EXCLUDED_TIERS:
            continue
        curve = release_day_curve(comparable)
        if curve is None:
            continue
        comp_one, by_hour, comp_peak = curve
        ratios = {}
        for clock_hour in range(1, 24):
            viewers = by_hour.get(clock_hour)
            if viewers is not None and viewers > 0:
                ratio = math.log(viewers / comp_one)
                ratios[clock_hour] = max(CLAMP_LO, min(CLAMP_HI, ratio))
        x = math.log(comp_one / comp_peak) if comp_peak else None
        launches.append({"ratios": ratios, "x": x})

    # --- Fallback: no usable comparables -> flat persistence. ---
    if not launches:
        anchor = hour_one if hour_one else (evening_peak or 1)
        return {
            "hours": [
                {
                    "hour": h,
                    "q": [
                        round(max(1.0, anchor * math.exp(z * 0.15 * math.sqrt(h - 1))), 2)
                        for z in Z_SCORES
                    ],
                }
                for h in forecast_hours
            ]
        }

    # --- Fallback: no hour-1 print -> anchor on the evening peak. ---
    if hour_one is None:
        anchor = evening_peak or 1
        hours = []
        for h in forecast_hours:
            clock_hour = h - 1
            # Ratios to each comparable's own evening peak.
            values = []
            for comparable in case.get("comparables", []):
                if tier_of(comparable) in EXCLUDED_TIERS:
                    continue
                curve = release_day_curve(comparable)
                if curve is None:
                    continue
                _, by_hour, comp_peak = curve
                if not comp_peak:
                    continue
                viewers = by_hour.get(clock_hour)
                if viewers is not None and viewers > 0:
                    values.append(max(CLAMP_LO, min(CLAMP_HI, math.log(viewers / comp_peak))))
            med, sigma = pooled_stats(values) if values else (0.0, 0.3)
            q = [anchor * math.exp(med + z * sigma * SPREAD_SHRINK) for z in Z_SCORES]
            hours.append({"hour": h, "q": _clean_quantiles(q)})
        return {"hours": hours}

    anchor = max(1, hour_one)

    # --- Dead-hour-1 split: which half of the comparables to learn from. ---
    target_x = math.log(hour_one / evening_peak) if evening_peak else None
    if target_x is not None:
        group = [
            launch for launch in launches
            if launch["x"] is not None and (launch["x"] <= SPLIT_X) == (target_x <= SPLIT_X)
        ]
        if len(group) < 3:
            group = launches
    else:
        group = launches

    hours = []
    for h in forecast_hours:
        clock_hour = h - 1
        values = [launch["ratios"][clock_hour] for launch in group
                  if clock_hour in launch["ratios"]]
        if len(values) < 3:
            values = [launch["ratios"][clock_hour] for launch in launches
                      if clock_hour in launch["ratios"]]
        med, sigma = pooled_stats(values) if values else (0.0, 0.3)
        q = [anchor * math.exp(med + z * sigma * SPREAD_SHRINK) for z in Z_SCORES]
        hours.append({"hour": h, "q": _clean_quantiles(q)})
    return {"hours": hours}


def round2(x):
    """x rounded to 2 decimals, half up (the house's convention: the
    baseline rounds the same way, so a forecast of 100000.005 becomes
    100000.01, not 100000.0 as Python's banker's round would give).

    A whole number comes back as an int, so json.dumps writes 1240
    where it would otherwise write 1240.0.
    """
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def _clean_quantiles(values):
    """Round to 2 decimals (half up), clamp to (0, 1e9], enforce
    non-decreasing."""
    cleaned = [max(0.01, min(1e9, round2(v))) for v in values]
    for i in range(1, len(cleaned)):
        cleaned[i] = max(cleaned[i], cleaned[i - 1])
    return cleaned


def emergency_forecast(input_dir, name):
    """Last-resort forecast for a case whose data cannot be parsed at
    all: flat persistence at hour 1's print if it can be read, else 1.0.
    A valid file that scores like the invalid-case fallback is strictly
    better than crashing and writing nothing (a missing output is an
    invalid case, and an entry with any invalid case takes no prize).
    """
    anchor = 1
    try:
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        first = next((k for k in case.get("known", []) if k.get("hour") == 1), None)
        if first is not None and first.get("viewers"):
            anchor = max(1, first["viewers"])
    except Exception:
        pass
    return {
        "hours": [
            {
                "hour": h,
                "q": [
                    round2(anchor * math.exp(z * 0.15 * math.sqrt(h - 1)))
                    for z in Z_SCORES
                ],
            }
            for h in range(2, 25)
        ]
    }


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    for name in sorted(os.listdir(input_dir)):
        if not name.endswith(".in"):
            continue
        out_name = name[:-3] + ".out"
        try:
            with open(os.path.join(input_dir, name), "r", encoding="utf-8") as fh:
                case = json.load(fh)
            result = forecast_case(case)
        except Exception:
            # Never crash on a malformed case: a missing output is an
            # invalid case, and an entry with any invalid case takes no
            # prize. The emergency forecast is always a valid file.
            result = emergency_forecast(input_dir, name)
        with open(os.path.join(output_dir, out_name), "w", encoding="utf-8") as fh:
            json.dump(result, fh, separators=(",", ":"))
            fh.write("\n")


if __name__ == "__main__":
    main()
