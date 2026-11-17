# GTA VI launch-day Twitch viewers forecaster

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/alfasurya91/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/alfasurya91/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

An entry for the make.fun bounty **gta6-twitch-ccu-forecast-01**: forecast
Grand Theft Auto VI's launch-day Twitch concurrent viewers hour by hour.
For each of hours 2 to 24 of release day (2026-11-19 UTC), forecast five
quantiles (5%, 25%, 50%, 75%, 95%) of that hour's concurrent viewers in the
"Grand Theft Auto VI" Twitch category. The prize pool is **$250 USDC**
(1st $50, 2nd $35, 3rd $25, 4th–10th $10, 11th–20th $7). Entries opened
**2026-11-17 06:00 UTC** and close **2026-11-19 01:00 UTC (08:00 WIB)**.
An entry only earns a prize if its score beats the best house entry
(a persistence baseline plus a house AI model). Score is mean pinball loss
on the log scale; lowest wins.

## The idea in plain words

The house baseline forecasts every hour equal to hour 1, with a spread that
widens like a random walk. That ignores the one thing every real launch day
has: a shape. Viewership dips in the small hours, then ramps as regions
unlock — console launches unlock at local midnight, so 00:00 UTC is only the
UK unlock and the US unlocks hours later — and as the day reaches the evening
peak, then decays overnight. This program learns that shape from the
"comparables" shipped in each case: the full hourly viewer curves of about
38 past big game launches.

For each comparable launch it forms, for every forecast hour h, the log-ratio
`ln(viewers in hour h / viewers in hour 1)`, pools those log-ratios across
comparables, and forecasts the median as
`hour-1 print × exp(median log-ratio for hour h)`, with the outer quantiles
taken from the spread of the log-ratios at that hour (median ± z × sigma,
sigma from the interquartile range, shrunk by 0.6).

Three guards keep one weird launch from wrecking the forecast:

1. **Tier filter.** Comparables carry a `Tier:` tag in their unlock notes.
   Early-access-contaminated launches (their peak happened days before
   release day, so their release-day curve is flat or falling) and "weak"
   launches are dropped, as is the target game's own pre-release history
   (it is not a launch). Only clean `comparable` launches are used.
2. **Dead-hour-1 split.** Hour 1 does not mean the same thing for every
   launch. If a launch's hour-1 print is already close to its pre-release
   evening peak, the audience was there before the day began and the day's
   ramp is small. If hour 1 is far below that evening peak, hour 1 was a
   dead hour and the main unlock wave is still coming, so the ramp is big.
   The program measures `x = ln(hour-1 / evening peak)` — the evening peak
   is the largest hourly viewership in the 24 hours before release day —
   and splits the comparables in two at `x = ln(0.5)`: hour 1 below half
   the evening peak means the wave is still ahead. The target's own `x`
   decides which half it is forecast from. (On the four practice cases
   this split is the difference between a 0.33 and a 0.18 mean pinball
   loss.) The notes' unlock times support the mechanism — launches whose
   surge already happened by 00:00 UTC (PC simultaneous unlock, or massive
   pre-release hype) sit in the live half; rolling-midnight console
   launches with quiet pre-release categories sit in the dead half — but
   the notes are free-form text and not uniformly parseable, so the program
   uses the measured consequence (hour 1 vs evening peak) rather than
   parsing them.
3. **Clamping.** Log-ratios are clamped to [-2, +3]: a launch may fall to
   ~1/7th or rise to ~20× its hour-1 print. Some tiny-category launches
   ramped 100×–2000× from a near-dead hour 1; without the clamp they would
   drag the whole distribution up. Missing or sparse hours in a comparable
   are simply skipped.

If the target has no usable pre-release history the split cannot be made
and the program pools all clean comparables. If the hour-1 print itself is
missing it anchors on the evening peak instead. If a case has no usable
comparables at all it falls back to flat persistence. A malformed case can
never crash the run: it falls back to a persistence forecast and still
writes a valid file (a missing output is an invalid case, and an entry with
any invalid case takes no prize).

## Practice score

Validated locally on the four practice cases (p00 Marvel's Wolverine,
p01 Red Dead Redemption 2, p02 God of War Ragnarök, p03 Marvel's
Spider-Man 2), scored with the contest's own pinball loss on the log scale.
The practice answers were recovered from the documented public source
(SullyGnome) and cross-checked two independent ways: the actual starter
program scores **0.387097** on them, matching the rules' "about 0.387",
and its board diagnostics reproduce the rules' "typical miss about 82.8%,
90% coverage about 0.783" exactly. The pinball implementation was verified
against the rules' worked example to 6 decimals.

| case | this entry | house baseline |
| ---- | ---------: | -------------: |
| p00 Marvel's Wolverine | 0.197657 | 0.743671 |
| p01 Red Dead Redemption 2 | 0.165814 | 0.286813 |
| p02 God of War Ragnarök | 0.143936 | 0.214579 |
| p03 Marvel's Spider-Man 2 | 0.197641 | 0.303324 |
| **overall** | **0.176262** | **0.387097** |

Typical miss 26–30% (baseline 42–189%); 90% coverage 0.78–0.91, mean
0.86 — near the honest 0.90, so the bands are not over-narrowed to flatter
the practice set. Every practice case beats the baseline; the overall loss
is 55% lower.

Honest caveat, as the rules state: the practice is graded on past launches
anyone can look up and pays nothing, so a practice score is no guide to the
final. The final case is GTA VI itself, a bigger launch than any practice
case, and its hour-1 print and evening peak will decide which half of the
comparables it is forecast from.

## Run it

Python 3.13, standard library only, no network, deterministic (two runs
write identical bytes), ~0.1–0.3 s per case (limit 8 s):

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `*.in` in the input directory in name order and writes one
`<id>.out` per case: `{"hours": [{"hour": 2, "q": [q05, q25, q50, q75,
q95]}, ...]}`, exactly 23 rows, hours 2..24 each once.

## Files

- `main.py` — the entry program (single file, standard library only).
- `README.md` — this file.
- `LICENSE` — MIT.
- `.makefun/owner.json` — the payout wallet.
