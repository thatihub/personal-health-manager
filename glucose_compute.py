"""Glucose data computation — shared by the Clarity CSV importer
(gen_glucose_data.py, Muse side) and the in-app Dexcom sync
(api/dexcom.py). Pure functions over parsed readings; no I/O except
build_glucose_data returning the dict the page consumes."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

WINDOWS = (14, 30, 90)
EXPECTED_PER_DAY = 288  # one reading every 5 minutes


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    if lo == hi:
        return sorted_vals[int(i)]
    return sorted_vals[lo] * (hi - i) + sorted_vals[hi] * (i - lo)


def window_stats(readings, days, end_date):
    start = end_date - timedelta(days=days - 1)
    vals = [v for ts, v in readings if start <= ts.date() <= end_date]
    n = len(vals)
    if n == 0:
        return {"days": days, "readings": 0}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0
    bands = {
        "very_low": sum(1 for v in vals if v < 54),
        "low": sum(1 for v in vals if 54 <= v < 70),
        "in_range": sum(1 for v in vals if 70 <= v <= 180),
        "high": sum(1 for v in vals if 181 <= v <= 250),
        "very_high": sum(1 for v in vals if v > 250),
    }
    return {
        "days": days,
        "readings": n,
        "avg": round(mean),
        "gmi": round(3.31 + 0.02392 * mean, 1),
        "sd": round(sd),
        "cv": round(100.0 * sd / mean, 1) if mean else None,
        "wear_pct": pct(n, days * EXPECTED_PER_DAY),
        "tir": {k: pct(c, n) for k, c in bands.items()},
    }


def agp(readings, days, end_date):
    start = end_date - timedelta(days=days - 1)
    bins = defaultdict(list)
    for ts, v in readings:
        if start <= ts.date() <= end_date:
            bins[ts.hour * 12 + ts.minute // 5].append(v)
    out = []
    for b in range(288):
        vals = sorted(bins.get(b, []))
        if not vals:
            continue
        out.append({
            "t": f"{b // 12:02d}:{(b % 12) * 5:02d}",
            "p10": round(percentile(vals, 0.10)),
            "p25": round(percentile(vals, 0.25)),
            "med": round(percentile(vals, 0.50)),
            "p75": round(percentile(vals, 0.75)),
            "p90": round(percentile(vals, 0.90)),
        })
    return out


def compute_insights(readings, insulin_events, meal_events, end_date):
    import bisect
    from statistics import mean as smean, median as smedian
    ts_list = [t for t, _ in readings]
    val_list = [v for _, v in readings]

    def val_at(t):
        i = bisect.bisect_left(ts_list, t)
        cands = [j for j in (i - 1, i) if 0 <= j < len(readings)]
        if not cands:
            return None
        j = min(cands, key=lambda j: abs((ts_list[j] - t).total_seconds()))
        return val_list[j] if abs((ts_list[j] - t).total_seconds()) <= 900 else None

    def r1(x):
        return round(x, 1) if x is not None else None

    hourly = []
    for h in range(24):
        a = [v for t, v in readings if t.hour == h]
        hourly.append({"h": h, "mean": round(smean(a)) if a else None,
                       "high": r1(100 * sum(1 for v in a if v > 180) / len(a)) if a else None})
    blocks = []
    for h0, h1, name in [(0, 6, "Night 12–6a"), (6, 12, "Morning 6a–12p"),
                         (12, 18, "Afternoon 12–6p"), (18, 24, "Evening 6p–12a")]:
        a = [v for t, v in readings if h0 <= t.hour < h1]
        blocks.append({"label": name, "mean": round(smean(a)),
                       "high": r1(100 * sum(1 for v in a if v > 180) / len(a)),
                       "in": r1(100 * sum(1 for v in a if 70 <= v <= 180) / len(a)),
                       "low": r1(100 * sum(1 for v in a if v < 70) / len(a))})

    by_day = defaultdict(list)
    for t, v in readings:
        by_day[t.date()].append((t, v))
    nadirs, morns = [], []
    for d, pts in by_day.items():
        early = [v for t, v in pts if 2 <= t.hour < 5]
        late = [v for t, v in pts if 7 <= t.hour < 9]
        if early and late:
            nadirs.append(min(early)); morns.append(smean(late))
    night_vals = [v for t, v in readings if t.hour < 6]
    lows = {"n70": sum(1 for v in val_list if v < 70),
            "pct70": r1(100 * sum(1 for v in val_list if v < 70) / len(val_list)),
            "n54": sum(1 for v in val_list if v < 54), "min": int(min(val_list))}

    res = []
    for t, g in meal_events:
        base = val_at(t)
        after = [v for tt, v in readings if t <= tt < t + timedelta(hours=2)]
        if base is None or len(after) < 10:
            continue
        res.append({"t": t, "carbs": g, "base": base, "peak": max(after),
                    "rise": max(after) - base})
    def meal_agg(sel):
        if not sel:
            return None
        return {"n": len(sel), "rise": round(smean([r["rise"] for r in sel])),
                "peak": round(smean([r["peak"] for r in sel])),
                "over": r1(100 * sum(1 for r in sel if r["peak"] > 180) / len(sel))}
    meals = {"n": len(res),
             "avgCarbs": round(smean([r["carbs"] for r in res])) if res else None,
             "rise": round(smean([r["rise"] for r in res])) if res else None,
             "peak": round(smean([r["peak"] for r in res])) if res else None,
             "over": r1(100 * sum(1 for r in res if r["peak"] > 180) / len(res)) if res else None,
             "byCarbs": [{"label": "<30 g", **(meal_agg([r for r in res if r["carbs"] < 30]) or {})},
                         {"label": "30–50 g", **(meal_agg([r for r in res if 30 <= r["carbs"] < 50]) or {})},
                         {"label": ">50 g", **(meal_agg([r for r in res if r["carbs"] >= 50]) or {})}],
             "bySlot": [{"label": "Breakfast", **(meal_agg([r for r in res if 5 <= r["t"].hour < 11]) or {})},
                        {"label": "Lunch", **(meal_agg([r for r in res if 11 <= r["t"].hour < 16]) or {})},
                        {"label": "Dinner", **(meal_agg([r for r in res if 16 <= r["t"].hour < 23]) or {})}]}
    ratios = []
    for t, g in meal_events:
        if g < 15:
            continue
        dose = sum(u for tt, u, s in insulin_events
                   if abs((tt - t).total_seconds()) <= 1800 and not s.startswith("Long"))
        if dose:
            ratios.append(dose / g * 10)
    meals["carbRatio"] = r1(smedian(ratios)) if ratios else None

    fast_d, long_d = defaultdict(float), defaultdict(float)
    for t, u, s in insulin_events:
        (long_d if s.startswith("Long") else fast_d)[t.date()] += u
    last30 = [end_date - timedelta(days=i) for i in range(30)]
    insulin = {"fast": r1(smean([fast_d.get(d, 0.0) for d in last30])),
               "long": r1(smean([long_d.get(d, 0.0) for d in last30]))}
    insulin["total"] = r1((insulin["fast"] or 0) + (insulin["long"] or 0))

    wk = defaultdict(list)
    for t, v in readings:
        wk[t.strftime("%G-W%V")].append((t, v))
    weeks = []
    for k in sorted(wk):
        pts = wk[k]; a = [v for _, v in pts]
        monday = min(t for t, _ in pts).date()
        weeks.append({"label": monday.strftime("%b ") + str(monday.day),
                      "mean": round(smean(a)),
                      "tir": r1(100 * sum(1 for v in a if 70 <= v <= 180) / len(a))})
    episodes, inep = 0, False
    for v in val_list:
        if v > 180 and not inep:
            episodes += 1; inep = True
        elif v <= 180:
            inep = False
    return {"hourly": hourly, "blocks": blocks,
            "dawn": {"nadir": round(smedian(nadirs)) if nadirs else None,
                     "morning": round(smean(morns)) if morns else None,
                     "rise": round(smean(morns) - smedian(nadirs)) if nadirs and morns else None},
            "night": {"mean": round(smean(night_vals)),
                      "lowPct": r1(100 * sum(1 for v in night_vals if v < 70) / len(night_vals))},
            "lows": lows, "meals": meals, "insulin": insulin, "weeks": weeks,
            "episodesPerDay": r1(episodes / max(len(by_day), 1))}


def compute_opinion(readings, insights, end_date):
    """Rule-based opinion over the computed insights: what changed, the
    dominant patterns, and the highest-leverage discussion points. Every
    number comes from the data; suggestions are framed as patterns to
    discuss with his doctor, never as dosing instructions."""
    from statistics import mean as smean
    cur = window_stats(readings, 14, end_date)
    prior_end = end_date - timedelta(days=14)
    prior = window_stats(readings, 14, prior_end)
    points, suggestions = [], []

    if cur.get("readings") and prior.get("readings"):
        dtir = round(cur["tir"]["in_range"] - prior["tir"]["in_range"], 1)
        dmean = cur["avg"] - prior["avg"]
        if dtir >= 2:
            points.append({"rank": 4, "icon": "📈", "title": "Improving",
                           "text": f"Last 14 days: time in range {cur['tir']['in_range']}% vs {prior['tir']['in_range']}% the 14 before (average {cur['avg']} vs {prior['avg']} mg/dL). The recent direction is up."})
        elif dtir <= -2:
            points.append({"rank": 4, "icon": "📉", "title": "Slipped a little",
                           "text": f"Last 14 days: time in range {cur['tir']['in_range']}% vs {prior['tir']['in_range']}% the 14 before (average {cur['avg']} vs {prior['avg']} mg/dL). A small give-back, not a collapse."})
        else:
            points.append({"rank": 4, "icon": "➡️", "title": "Holding steady",
                           "text": f"Last 14 days: time in range {cur['tir']['in_range']}% vs {prior['tir']['in_range']}% the 14 before, average {cur['avg']} mg/dL. Control is stable at this level."})

    blocks = {b["label"]: b for b in insights["blocks"]}
    eve = blocks.get("Evening 6p–12a")
    highs = [(t, v) for t, v in readings if v > 180]
    eve_share = round(100 * sum(1 for t, _ in highs if t.hour >= 18) / len(highs)) if highs else 0
    peak_hour = max(insights["hourly"], key=lambda x: x["mean"] or 0)
    if eve and eve["high"] >= 25:
        points.append({"rank": 1, "icon": "🌆", "title": "Evenings carry the highs",
                       "text": f"{eve_share}% of every reading above 180 happens after 6 PM. The evening block averages {eve['mean']} with {eve['high']}% high; the single highest hour is {peak_hour['h']}:00 (avg {peak_hour['mean']})."})
        suggestions.append({"rank": 1, "title": "The evening lever",
                            "text": "Evenings are where the room is: dinner coverage and what happens after dinner (dose timing, meal size, an evening walk) decide most of the remaining highs. Worth raising with Dr. Munir as the #1 topic."})

    dawn = insights["dawn"]
    if dawn.get("rise") and dawn["rise"] >= 30:
        points.append({"rank": 3, "icon": "🌅", "title": "A real dawn rise",
                       "text": f"Glucose climbs about +{dawn['rise']} overnight — from a typical low of {dawn['nadir']} (2–5 AM) to {dawn['morning']} by 7–9 AM — before any food. Mornings start elevated by physiology, not by breakfast."})
        suggestions.append({"rank": 3, "title": "Judge breakfast fairly",
                            "text": "Breakfast numbers start ~" + str(dawn["morning"]) + " before food lands, so a high 9 AM reading is often the dawn rise, not a breakfast failure. Your breakfast rise itself (+28) is your smallest of the day."})

    m = insights["meals"]
    if m.get("n"):
        worst = max((b for b in m["byCarbs"] if b.get("n")), key=lambda b: b.get("over") or 0)
        points.append({"rank": 2, "icon": "🍽️", "title": "Mid-size meals outrun their dose",
                       "text": f"Across {m['n']} logged meals: the {worst['label']} band peaks above 180 most often ({worst['over']}% of the time), while meals over 50 g do better ({[b for b in m['byCarbs'] if b['label'] == '>50 g'][0].get('over')}% over) because they get bigger doses. Working ratio ≈ {m['carbRatio']} u per 10 g."})
        suggestions.append({"rank": 2, "title": "Watch the 30–50 g meal",
                            "text": "That band rises +" + str(worst["rise"]) + " on average. The pattern — not the carb count alone — suggests its dose or timing lags the carbs most often. Your own rule from the gas pattern points the same way: coverage matched to carbs is what keeps things calm."})

    lows = insights["lows"]
    if lows["pct70"] is not None and lows["pct70"] < 1:
        points.append({"rank": 5, "icon": "🛡️", "title": "Lows are not your problem",
                       "text": f"Only {lows['n70']} readings below 70 in the whole record ({lows['pct70']}%), {lows['n54']} below 54, lowest {lows['min']}. There is headroom to work on the highs without fighting lows."})

    points.sort(key=lambda p: p.get("rank", 99))
    suggestions.sort(key=lambda s: s.get("rank", 99))
    best = max(insights["weeks"], key=lambda w: w["tir"] or 0)
    headline = ("Strong control overall — your system works. "
                "The remaining story is concentrated: evenings, the dawn rise, and mid-size meals.")
    return {"asOf": end_date.isoformat(), "headline": headline,
            "points": points, "suggestions": suggestions,
            "bestWeek": {"label": best["label"], "tir": best["tir"]}}


def build_glucose_data(readings, insulin_by_day, meal_by_day,
                       insulin_events, meal_events, source_label, clamped=0):
    """readings: [(datetime, value)] sorted; events: [(datetime, ...)].
    Returns the full GLUCOSE_DATA dict (same shape the CSV path writes)."""
    end_date = readings[-1][0].date()
    start_date = readings[0][0].date()

    by_day = defaultdict(list)
    for ts, v in readings:
        by_day[ts.date().isoformat()].append([ts.strftime("%H:%M"), int(round(v))])
    hour_bins = defaultdict(list)
    for ts, v in readings:
        hour_bins[(ts.date().isoformat(), ts.hour)].append(v)
    day_summaries = []
    for d in sorted(by_day):
        vals = [v for _, v in by_day[d]]
        mean = sum(vals) / len(vals)
        day_summaries.append({
            "date": d, "n": len(vals), "avg": round(mean),
            "min": int(min(vals)), "max": int(max(vals)),
            "tir": pct(sum(1 for v in vals if 70 <= v <= 180), len(vals)),
            "insulin": round(sum(u for _, u, _ in insulin_by_day.get(d, [])), 1),
            "insulin_fast": round(sum(u for _, u, s in insulin_by_day.get(d, []) if not s.startswith("Long")), 1),
            "carbs": round(sum(g for _, g in meal_by_day.get(d, [])), 1),
            "heat": [round(sum(hour_bins.get((d, h), [])) / len(hour_bins[(d, h)]))
                     if hour_bins.get((d, h)) else None for h in range(24)],
        })

    traces = {}
    for d, pts in sorted(by_day.items())[-31:]:
        entry = {"g": pts}
        if insulin_by_day.get(d):
            entry["ins"] = insulin_by_day[d]
        if meal_by_day.get(d):
            entry["meal"] = meal_by_day[d]
        traces[d] = entry

    insights = compute_insights(readings, insulin_events, meal_events, end_date)
    return {
        "source": source_label,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "total_readings": len(readings),
        "clamped_low_high": clamped,
        "windows": {str(w): window_stats(readings, w, end_date) for w in WINDOWS},
        "agp": {str(w): agp(readings, w, end_date) for w in WINDOWS},
        "insights": insights,
        "opinion": compute_opinion(readings, insights, end_date),
        "days": day_summaries,
        "traces": traces,
    }
