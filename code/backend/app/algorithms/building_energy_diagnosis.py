"""独立建筑用电诊断原型 / Standalone building-energy diagnosis.

Only this Python file, numpy/pandas/scikit-learn, and explicit input data are
needed. No experiment folders, old choice JSONs, application database or server
are read. Embedded profiles preserve the previously validated three-building
choices; they do not claim automatic selection for an unseen building.

The quantiles, six-hour blocks and V6 rules are unchanged. This is diagnosis and
review allocation, not causal savings, device control or global energy optimum.

Example (PowerShell, one line):
  & E:/anaconda/envs/aic/python.exe building_energy_diagnosis.py --data-root E:/vscode_python/aic_building --output ./fresh_results

--config optionally accepts a complete replacement JSON configuration. Such a
profile needs separate validation; the default has provenance in the embedded
validation metrics. Existing output directories are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.metadata
import itertools
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss

QUANTILE_COLUMNS = ["q20", "q50", "q80"]
CALENDAR = ["hour", "dow", "weekend", "year_sin", "year_cos"]
WEATHER = ["temperature", "temperature_24h"]
POLICIES = ["prefix_cumulative", "prefix_28d", "guarded_28d"]
STATUS_NAMES = {
    "data_unavailable": "数据不足", "reference_unavailable": "模型参照不足",
    "model_gate_failed": "验证门槛未通过", "no_model_excess": "未达到模型偏高条件",
    "historic_support_insufficient": "去年参照不足", "reference_conflict": "参照存在分歧",
    "recent_support_insufficient": "近期参照不足", "recurrence_insufficient": "尚未重复够",
    "evidence_intermediate": "两期偏高证据不足", "review_candidate": "进入核查候选",
}

DEFAULT_CONFIG = {'buildings': ['Bobcat_education_Coleman', 'Bobcat_office_Alma', 'Bobcat_lodging_Melaine'],
 'train': ['2016-01-01', '2017-01-01'],
 'validation': ['2017-01-01', '2017-07-01'],
 'test': ['2017-07-01', '2018-01-01'],
 'electricity_source': 'dataset/raw/electricity.csv',
 'weather_source': 'dataset/weather.csv',
 'metadata_source': 'dataset/metadata.csv',
 'site': 'Bobcat',
 'quality': {'zero_run_hours': 24, 'zero_run_sensitivity_hours': [12, 24, 48]},
 'rolling': {'window_days': 28,
             'minimum_past_observations': 168,
             'calendar_group_minimum': 4,
             'quantiles': [0.2, 0.5, 0.8]},
 'models': {'quantile_boosting': {'n_estimators': 120, 'learning_rate': 0.05, 'max_depth': 3, 'random_state': 42}},
 'candidate': {'block_hours': 6,
               'minimum_hours_above_q80': 3,
               'temperature_tolerance_c': 3,
               'temperature_24h_tolerance_c': 5,
               'historic_minimum_days': 15,
               'recent_minimum_days': 5,
               'maximum_reference_days': 30,
               'recent_window_days': 28,
               'minimum_recurrence_weeks': 3,
               'recurrence_window_days': 28,
               'both_periods_fraction_strictly_lower': 0.8},
 'weekly_slots': 3,
 'version': 'standalone-1.0; scientific decisions frozen at replay-v6',
 'profiles': {'Bobcat_education_Coleman': {'base_model': 'calendar_baseline',
                                           'reference_method': 'base_with_rolling_calibration',
                                           'validation_reference_gate_passed': True,
                                           'validation_metrics': {'building': 'Bobcat_education_Coleman',
                                                                  'model': 'base_with_rolling_calibration',
                                                                  'split': 'validation',
                                                                  'n': 3054,
                                                                  'mae_kwh': 10.314511661755075,
                                                                  'wape_pct': 8.634732446507588,
                                                                  'rmse_kwh': 16.278487186759154,
                                                                  'normalized_pinball': 0.0380023064559502,
                                                                  'pinball_q20': 4.048201741715783,
                                                                  'pinball_q50': 5.157255830877538,
                                                                  'pinball_q80': 4.413096408382449,
                                                                  'cdf_q20': 0.22167648984937788,
                                                                  'cdf_q50': 0.5098231827111984,
                                                                  'cdf_q80': 0.8005893909626719,
                                                                  'central_60_coverage': 0.5792403405370007,
                                                                  'mean_interval_width_kwh': 24.056304080550102,
                                                                  'crossing_rate': 0.0,
                                                                  'quantile_bracket_q20': {'strict': 0.22134905042567124,
                                                                                           'inclusive': 0.22167648984937788,
                                                                                           'error': 0.02134905042567123},
                                                                  'quantile_bracket_q80': {'strict': 0.8005893909626719,
                                                                                           'inclusive': 0.8005893909626719,
                                                                                           'error': 0.0005893909626718541},
                                                                  'corrected_gate_passed': True}},
              'Bobcat_office_Alma': {'base_model': 'calendar_baseline',
                                     'reference_method': 'recent_calendar_28d',
                                     'validation_reference_gate_passed': True,
                                     'validation_metrics': {'building': 'Bobcat_office_Alma',
                                                            'model': 'recent_calendar_28d',
                                                            'split': 'validation',
                                                            'n': 3059,
                                                            'mae_kwh': 1.0116055083360576,
                                                            'wape_pct': 21.82607691156559,
                                                            'rmse_kwh': 1.3745613467478843,
                                                            'normalized_pinball': 0.08884400801806538,
                                                            'pinball_q20': 0.35138302190258264,
                                                            'pinball_q50': 0.5058027541680288,
                                                            'pinball_q80': 0.3781496659038901,
                                                            'cdf_q20': 0.4040536122915986,
                                                            'cdf_q50': 0.6433474991827395,
                                                            'cdf_q80': 0.8277214776070612,
                                                            'central_60_coverage': 0.6407322654462243,
                                                            'mean_interval_width_kwh': 1.623145230467473,
                                                            'crossing_rate': 0.0,
                                                            'quantile_bracket_q20': {'strict': 0.1869892121608369,
                                                                                     'inclusive': 0.4040536122915986,
                                                                                     'error': 0.0},
                                                            'quantile_bracket_q80': {'strict': 0.6793069630598235,
                                                                                     'inclusive': 0.8277214776070612,
                                                                                     'error': 0.0},
                                                            'corrected_gate_passed': True}},
              'Bobcat_lodging_Melaine': {'base_model': 'weather_gb_leaf30',
                                         'reference_method': 'base_with_rolling_calibration',
                                         'validation_reference_gate_passed': True,
                                         'validation_metrics': {'building': 'Bobcat_lodging_Melaine',
                                                                'model': 'base_with_rolling_calibration',
                                                                'split': 'validation',
                                                                'n': 3053,
                                                                'mae_kwh': 2.926442224402334,
                                                                'wape_pct': 7.601889088254816,
                                                                'rmse_kwh': 4.37879055585453,
                                                                'normalized_pinball': 0.03061971135826604,
                                                                'pinball_q20': 1.0684570529838322,
                                                                'pinball_q50': 1.463221112201167,
                                                                'pinball_q80': 1.0045540204287247,
                                                                'cdf_q20': 0.2250245660006551,
                                                                'cdf_q50': 0.5276776940714052,
                                                                'cdf_q80': 0.7936455944972158,
                                                                'central_60_coverage': 0.5686210284965607,
                                                                'mean_interval_width_kwh': 5.187906675619904,
                                                                'crossing_rate': 0.0,
                                                                'quantile_bracket_q20': {'strict': 0.2250245660006551,
                                                                                         'inclusive': 0.2250245660006551,
                                                                                         'error': 0.025024566000655085},
                                                                'quantile_bracket_q80': {'strict': 0.7936455944972158,
                                                                                         'inclusive': 0.7936455944972158,
                                                                                         'error': 0.006354405502784211},
                                                                'corrected_gate_passed': True}}}}


def save(path, value):
    def native(obj):
        if isinstance(obj, (np.integer, np.floating, np.bool_)):
            return obj.item()
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        raise TypeError(type(obj).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=native), encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zero_run_mask(values: pd.Series, threshold: int) -> pd.Series:
    zero = values.eq(0)
    groups = zero.ne(zero.shift(fill_value=False)).cumsum()
    lengths = zero.groupby(groups).transform("sum")
    return zero & lengths.ge(threshold)


def split_mask(frame: pd.DataFrame, limits: list[str]):
    return (frame.index >= pd.Timestamp(limits[0])) & (frame.index < pd.Timestamp(limits[1]))


def prepare(config: dict, output: Path, data_root: Path):
    buildings = config["buildings"]
    power_path = data_root / config["electricity_source"]
    weather_path = data_root / config["weather_source"]
    metadata_path = data_root / config["metadata_source"]
    power = pd.read_csv(power_path, usecols=["timestamp", *buildings], parse_dates=["timestamp"])
    if power["timestamp"].duplicated().any():
        raise ValueError("Duplicate electricity wall-clock timestamps require explicit resolution")
    power = power.set_index("timestamp").sort_index()
    clock = pd.date_range(config["train"][0], config["test"][1], freq="h", inclusive="left")
    power = power.reindex(clock)
    weather = pd.read_csv(weather_path, usecols=["timestamp", "site_id", "airTemperature"])
    weather = weather[weather.site_id.eq(config["site"])].copy()
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    if weather["timestamp"].duplicated().any():
        raise ValueError("Duplicate weather wall-clock timestamps require explicit resolution")
    weather = weather.set_index("timestamp").reindex(clock)
    temperature = pd.to_numeric(weather.airTemperature, errors="coerce")
    temperature_24h = temperature.rolling(24, min_periods=18).mean()
    metadata = pd.read_csv(metadata_path, dtype=str).set_index("building_id")
    quality = []
    frames = {}
    for building in buildings:
        target = pd.to_numeric(power[building], errors="coerce")
        frame = pd.DataFrame({"y": target, "temperature": temperature, "temperature_24h": temperature_24h}, index=clock)
        frame["hour"] = clock.hour
        frame["dow"] = clock.dayofweek
        frame["weekend"] = (clock.dayofweek >= 5).astype(int)
        # Month/day rather than raw day-of-year avoids a one-day leap-year phase offset.
        phase = (clock.month - 1) / 12 + (clock.day - 1) / 372
        frame["year_sin"] = np.sin(2 * np.pi * phase)
        frame["year_cos"] = np.cos(2 * np.pi * phase)
        frame["hour_week"] = clock.dayofweek * 24 + clock.hour
        frame["long_zero"] = False
        for split in ["train", "validation", "test"]:
            mask = split_mask(frame, config[split])
            # A training-tail zero must not be labelled using future target values.
            frame.loc[mask, "long_zero"] = zero_run_mask(target.loc[mask], config["quality"]["zero_run_hours"])
        frame["good"] = np.isfinite(target) & target.ge(0) & ~frame.long_zero & frame[WEATHER].notna().all(axis=1)
        for split in ["train", "validation", "test"]:
            part = frame.loc[split_mask(frame, config[split])]
            quality.append({
                "building": building, "split": split, "clock_hours": len(part),
                "nonempty_target": int(part.y.notna().sum()), "zero_hours": int(part.y.eq(0).sum()),
                "negative_hours": int(part.y.lt(0).sum()), "long_zero_hours": int(part.long_zero.sum()),
                "weather_or_history_missing": int(part[WEATHER].isna().any(axis=1).sum()),
                "usable_hours": int(part.good.sum()),
                "zero_sensitivity": {str(h): int(zero_run_mask(part.y, h).sum()) for h in config["quality"]["zero_run_sensitivity_hours"]},
            })
        frames[building] = frame
        frame.to_csv(output / f"prepared_{building}.csv", index_label="local_timestamp")
        print("Prepared", building, {s: int((split_mask(frame, config[s]) & frame.good).sum()) for s in ["train", "validation", "test"]}, flush=True)
    audit = {
        "quality": quality,
        "metadata": metadata.loc[buildings, ["site_id", "primaryspaceusage", "sub_primaryspaceusage", "sqm", "timezone"]].reset_index().to_dict("records"),
        "source_sha256": {str(p.relative_to(data_root)): sha256(p) for p in [power_path, weather_path, metadata_path]},
        "note": "Counts of exclusions overlap; quality status is an operational screening choice, not a confirmed failure label.",
    }
    save(output / "step01_data_audit.json", audit)
    return frames, audit


def clock_prediction(train: pd.DataFrame, evaluation: pd.DataFrame):
    table = train.groupby("hour_week").y.quantile([0.2, 0.5, 0.8]).unstack()
    prediction = table.reindex(evaluation.hour_week).to_numpy()
    if not np.isfinite(prediction).all():
        raise ValueError("Clock baseline has unsupported hour-of-week cells")
    return prediction


def evaluate(y, prediction):
    y = np.asarray(y, dtype=float)
    error = y - prediction[:, 1]
    scale = max(float(np.mean(np.abs(y))), 1e-12)
    losses = [float(mean_pinball_loss(y, prediction[:, i], alpha=q)) for i, q in enumerate([0.2, 0.5, 0.8])]
    return {
        "n": len(y), "mae_kwh": float(np.mean(np.abs(error))),
        "wape_pct": float(100 * np.sum(np.abs(error)) / max(np.sum(np.abs(y)), 1e-12)),
        "rmse_kwh": float(np.sqrt(np.mean(error ** 2))),
        "normalized_pinball": float(np.mean(losses) / scale),
        "pinball_q20": losses[0], "pinball_q50": losses[1], "pinball_q80": losses[2],
        "cdf_q20": float(np.mean(y <= prediction[:, 0])),
        "cdf_q50": float(np.mean(y <= prediction[:, 1])),
        "cdf_q80": float(np.mean(y <= prediction[:, 2])),
        "central_60_coverage": float(np.mean((y >= prediction[:, 0]) & (y <= prediction[:, 2]))),
        "mean_interval_width_kwh": float(np.mean(prediction[:, 2] - prediction[:, 0])),
        "crossing_rate": float(np.mean((prediction[:, 0] > prediction[:, 1]) | (prediction[:, 1] > prediction[:, 2]))),
    }


def fit_predict(train, evaluation, features, leaf, config):
    params = config["models"]["quantile_boosting"]
    results = []
    for quantile in config["quantiles"]:
        model = GradientBoostingRegressor(
            loss="quantile", alpha=quantile, n_estimators=params["n_estimators"],
            learning_rate=params["learning_rate"], max_depth=params["max_depth"],
            min_samples_leaf=leaf, random_state=params["random_state"],
        )
        model.fit(train[features], train.y)
        results.append(model.predict(evaluation[features]))
    return np.column_stack(results)


def reference_blocks(train, block_hours):
    data = train.copy()
    data["date"] = data.index.normalize()
    data["block"] = data.index.hour // block_hours
    reference = data.groupby(["date", "block"]).agg(
        count=("y", "size"), energy=("y", "sum"), temperature=("temperature", "mean"),
        temperature_24h=("temperature_24h", "mean"), weekend=("weekend", "first"),
    ).reset_index()
    return reference[reference["count"].eq(block_hours)].copy()


def causal_quality(frame, zero_limit=24):
    """Never retroactively label earlier zero readings using a later observation."""
    streak, lengths = 0, []
    for value in frame.y:
        streak = streak + 1 if np.isfinite(value) and value == 0 else 0
        lengths.append(streak)
    long_zero = pd.Series(np.array(lengths) >= zero_limit, index=frame.index)
    good = np.isfinite(frame.y) & frame.y.ge(0) & ~long_zero
    good &= frame[["temperature", "temperature_24h"]].notna().all(axis=1)
    return good, long_zero


def summarize_reference(rows, observed):
    if not rows:
        return {"n": 0, "median_kwh": None, "fraction_strictly_lower": None, "examples": []}
    energy = np.array([r["energy"] for r in rows])
    return {"n": len(rows), "median_kwh": float(np.median(energy)),
            "fraction_strictly_lower": float(np.mean(energy < observed)),
            "examples": [{"start": r["start"], "end": r["end"], "energy_kwh": r["energy"]} for r in rows[:3]],
            "all_reference_starts": [r["start"] for r in rows], "all_reference_ends": [r["end"] for r in rows]}


class DiagnosticReplay:
    def __init__(self, config, historic, recent_initial, gates):
        self.config = config
        self.settings = config["candidate"]
        self.historic = historic
        self.recent = deepcopy(recent_initial)
        self.gates = gates
        self.seen_weeks = defaultdict(set)
        self.recurrence = defaultdict(list)
        self.records = []
        self.weekly_results = []
        self.priorities = []
        self.last_time = pd.Timestamp(config["test"][0])
        self.replay_start = self.last_time
        self.next_closure = self.last_time.normalize() - pd.Timedelta(days=self.last_time.dayofweek) + pd.Timedelta(days=7)
        for rows in self.recent.values():
            assert all(pd.Timestamp(r["end"]) <= self.replay_start for r in rows)
        for rows in self.historic.values():
            assert all(pd.Timestamp(r["end"]) <= pd.Timestamp(config["train"][1]) for r in rows)

    def match(self, rows, event, recent=False):
        start = pd.Timestamp(event["start"])
        selected = []
        for row in rows:
            if row["block"] != event["block"] or row["weekend"] != event["weekend"]:
                continue
            if recent and not (pd.Timestamp(row["start"]) >= start - pd.Timedelta(days=self.settings["recent_window_days"])
                               and pd.Timestamp(row["end"]) <= start):
                continue
            dt = abs(row["temperature"] - event["temperature"])
            dt24 = abs(row["temperature_24h"] - event["temperature_24h"])
            if dt <= self.settings["temperature_tolerance_c"] and dt24 <= self.settings["temperature_24h_tolerance_c"]:
                selected.append((dt + dt24, row["start"], row))
        selected.sort(key=lambda item: item[:2])
        return [item[2] for item in selected[:self.settings["maximum_reference_days"]]]

    def ingest(self, building, block):
        """Ingest one completed non-overlapping six-hour block; decision never changes."""
        now = pd.Timestamp(block["end"])
        if now < self.last_time:
            raise ValueError("Blocks must arrive in completion-time order")
        if pd.Timestamp(block["start"]) < self.replay_start:
            raise ValueError("Warm-up blocks belong in recent_initial, not online event history")
        self.last_time = now
        case = {"case_id": f"{building}__{pd.Timestamp(block['start']).strftime('%Y%m%d_%H')}",
                "building": building, "scope": "building", "energy_type": "electricity", "timezone": "US/Mountain",
                **deepcopy(block), "decision_available_at": block["end"],
                "model_choice_available_at": self.config["test"][0] + "T00:00:00",
                "policy_eligibility": {p: False for p in POLICIES}, "reasons": [],
                "recurrence": {"cumulative_distinct_weeks": 0, "recent_distinct_weeks": 0, "recent_event_ends": []},
                "comparisons": {}, "model_excess_supported": False,
                "human_review": {"status": "pending", "confirmed_cause": None},
                "control_extension": {"control_action": None, "indoor_temperature_c": None, "comfort_result": None, "verified_savings_kwh": None}}
        case["evidence_score_kwh"] = None
        if not block["quality_complete"]:
            status = "data_unavailable"
        elif not block["reference_complete"]:
            status = "reference_unavailable"
        elif not self.gates[building]:
            status = "model_gate_failed"
        else:
            values, q80 = np.array(block["hourly_energy_kwh"]), np.array(block["hourly_q80_kwh"])
            case["hours_above_q80"] = int(np.sum(values > q80))
            case["evidence_score_kwh"] = float(np.maximum(values - q80, 0).sum())
            if case["hours_above_q80"] < self.settings["minimum_hours_above_q80"]:
                status = "no_model_excess"
            else:
                history = summarize_reference(self.match(self.historic[building], block), block["energy"])
                case["comparisons"]["historic_2016"] = history
                if history["n"] < self.settings["historic_minimum_days"]:
                    status = "historic_support_insufficient"
                else:
                    case["model_excess_supported"] = True
                    key = (building, block["block"], block["weekend"])
                    self.seen_weeks[key].add(block["iso_week"])
                    lower = now - pd.Timedelta(days=self.settings["recurrence_window_days"])
                    self.recurrence[key] = [(end, week) for end, week in self.recurrence[key] if end >= lower]
                    self.recurrence[key].append((now, block["iso_week"]))
                    recent_weeks = len({week for _, week in self.recurrence[key]})
                    case["recurrence"] = {"cumulative_distinct_weeks": len(self.seen_weeks[key]), "recent_distinct_weeks": recent_weeks,
                                          "recent_event_ends": [end.isoformat() for end, _ in self.recurrence[key]]}
                    case["policy_eligibility"]["prefix_cumulative"] = len(self.seen_weeks[key]) >= self.settings["minimum_recurrence_weeks"]
                    case["policy_eligibility"]["prefix_28d"] = recent_weeks >= self.settings["minimum_recurrence_weeks"]
                    recent = summarize_reference(self.match(self.recent[building], block, recent=True), block["energy"])
                    case["comparisons"]["recent_before_event"] = recent
                    recent_supported = recent["n"] >= self.settings["recent_minimum_days"]
                    conflict = block["energy"] <= history["median_kwh"] or (recent_supported and block["energy"] <= recent["median_kwh"])
                    both_high = recent_supported and min(history["fraction_strictly_lower"], recent["fraction_strictly_lower"]) >= self.settings["both_periods_fraction_strictly_lower"]
                    reasons = []
                    if conflict:
                        reasons.append("reference_conflict")
                    if not recent_supported:
                        reasons.append("recent_support_insufficient")
                    if not case["policy_eligibility"]["prefix_28d"]:
                        reasons.append("recurrence_insufficient")
                    if recent_supported and not both_high and not conflict:
                        reasons.append("evidence_intermediate")
                    status = reasons[0] if reasons else "review_candidate"
                    case["reasons"] = reasons
                    case["policy_eligibility"]["guarded_28d"] = status == "review_candidate"
        case["status"] = status
        case["status_plain"] = STATUS_NAMES[status]
        if not case["reasons"] and status != "review_candidate":
            case["reasons"] = [status]
        if block["quality_complete"]:
            # Current block enters state only after its own decision, never its own comparator.
            self.recent[building].append({k: block[k] for k in ["start", "end", "block", "weekend", "energy", "temperature", "temperature_24h"]})
        cutoff = now - pd.Timedelta(days=self.settings["recent_window_days"] + 1)
        self.recent[building] = [r for r in self.recent[building] if pd.Timestamp(r["end"]) >= cutoff]
        self.records.append(case)
        return deepcopy(case)

    def publish_closed_weeks(self, now):
        """Call after ALL buildings' blocks ending at this instant were ingested."""
        now = pd.Timestamp(now)
        while self.next_closure <= now:
            end = self.next_closure
            start = end - pd.Timedelta(days=7)
            complete = start >= self.replay_start
            events = [r for r in self.records if start <= pd.Timestamp(r["start"]) < end and pd.Timestamp(r["end"]) <= end]
            for policy in POLICIES:
                eligible = [r for r in events if r["policy_eligibility"][policy]] if complete else []
                selected = sorted(eligible, key=lambda r: (-r["evidence_score_kwh"], r["building"], r["start"]))[:self.config["weekly_slots"]]
                objective = sum(r["evidence_score_kwh"] for r in selected)
                enumerated = len(eligible) <= 20
                if enumerated:
                    exhaustive = max((sum(r["evidence_score_kwh"] for r in subset) for subset in itertools.combinations(eligible, min(len(eligible), self.config["weekly_slots"]))), default=0.0)
                    assert np.isclose(objective, exhaustive)
                self.weekly_results.append({"policy": policy, "week_start": start.isoformat(), "week_end": end.isoformat(),
                                            "available_at": end.isoformat(), "complete_week": complete,
                                            "skip_reason": None if complete else "replay_started_inside_week",
                                            "eligible_cases": len(eligible), "selected_cases": len(selected),
                                            "selected_case_ids": [r["case_id"] for r in selected],
                                            "objective_evidence_kwh": objective, "exact_enumeration_checked": enumerated})
                for rank, case in enumerate(selected, 1):
                    self.priorities.append({**deepcopy(case), "policy": policy, "weekly_rank": rank, "weekly_available_at": end.isoformat(),
                                            "interpretation": "Priority for retrospective human investigation; not a device command or verified savings."})
            self.next_closure += pd.Timedelta(days=7)


def ordered(values):
    return np.maximum(0, np.sort(np.asarray(values, dtype=float), axis=1))


def reference_sequence(frame, base, method, config):
    quality, zeros = causal_quality(frame, config["quality"]["zero_run_hours"])
    data = frame.loc[base.index].copy()
    data["good_online"] = quality.reindex(data.index)
    data["long_zero_online"] = zeros.reindex(data.index)
    settings = config["rolling"]
    raw = ordered(base.to_numpy())
    y = data.y.where(data.good_online)
    if method == "recent_calendar_28d":
        prediction = pd.DataFrame(np.nan, index=data.index, columns=QUANTILE_COLUMNS)
        for _, group in data.groupby(["hour", "weekend"]):
            for column, quantile in zip(QUANTILE_COLUMNS, settings["quantiles"]):
                prediction.loc[group.index, column] = y.loc[group.index].rolling(
                    f"{settings['window_days']}D", closed="left", min_periods=settings["calendar_group_minimum"]
                ).quantile(quantile)
        values = prediction.to_numpy()
    elif method == "base_with_rolling_calibration":
        offsets = []
        for j, quantile in enumerate(settings["quantiles"]):
            residual = pd.Series(y.to_numpy() - raw[:, j], index=data.index)
            offsets.append(residual.rolling(f"{settings['window_days']}D", closed="left",
                                           min_periods=settings["minimum_past_observations"]).quantile(quantile).to_numpy())
        values = raw + np.column_stack(offsets)
    else:
        raise ValueError(method)
    data[QUANTILE_COLUMNS] = ordered(values)
    return data


def blocks_from_hours(data):
    blocks = []
    for start, part in data.groupby(pd.Grouper(freq="6h")):
        if len(part) != 6:
            continue
        end = start + pd.Timedelta(hours=6)
        iso = start.isocalendar()
        quality = bool(part.good_online.all())
        reference = bool(np.isfinite(part[QUANTILE_COLUMNS]).all(axis=None))
        finite_y = bool(np.isfinite(part.y).all() and part.y.ge(0).all())
        blocks.append({"start": start.isoformat(), "end": end.isoformat(), "block": int(start.hour // 6),
                       "weekend": int(start.dayofweek >= 5), "iso_week": f"{iso.year}-W{iso.week:02d}",
                       "energy": float(part.y.sum()) if finite_y else None,
                       "temperature": float(part.temperature.mean()) if part.temperature.notna().all() else None,
                       "temperature_24h": float(part.temperature_24h.mean()) if part.temperature_24h.notna().all() else None,
                       "quality_complete": quality, "reference_complete": reference,
                       "good_hours": int(part.good_online.sum()),
                       "hourly_energy_kwh": [float(y) if np.isfinite(y) else None for y in part.y],
                       "hourly_q20_kwh": part.q20.to_list() if reference else [None] * 6,
                       "hourly_q80_kwh": part.q80.to_list() if reference else [None] * 6,
                       "latest_reference_target_history_end": part.index.max().isoformat(),
                       "reference_weather_available_at": end.isoformat()})
    return blocks


def historic_blocks(frame, config):
    train = frame.loc[(frame.index < config["train"][1]) & frame.good]
    reference = reference_blocks(train, config["candidate"]["block_hours"])
    result = []
    for row in reference.itertuples(index=False):
        start = row.date + pd.Timedelta(hours=int(row.block) * 6)
        result.append({"start": start.isoformat(), "end": (start + pd.Timedelta(hours=6)).isoformat(),
                       "block": int(row.block), "weekend": int(row.weekend), "energy": float(row.energy),
                       "temperature": float(row.temperature), "temperature_24h": float(row.temperature_24h)})
    return result


def replay(config, historic, initial, streams, gates, until=None):
    engine = DiagnosticReplay(config, historic, initial, gates)
    limit = pd.Timestamp(until or config["test"][1])
    arrivals = sorted([(r["end"], b, r) for b, rows in streams.items() for r in rows if pd.Timestamp(r["end"]) <= limit], key=lambda row: row[:2])
    for end, group in itertools.groupby(arrivals, key=lambda row: row[0]):
        for _, building, block in group:
            engine.ingest(building, block)
        engine.publish_closed_weeks(end)
    return engine


def toy_gate_checks(config):
    clock = pd.date_range("2017-06-01", periods=48, freq="h")
    toy = pd.DataFrame({"y": 0., "temperature": 20., "temperature_24h": 20.}, index=clock)
    short = causal_quality(toy.iloc[:23])[0]
    long = causal_quality(toy)[0]
    assert short.all() and long.iloc[:23].all() and not long.iloc[23:].any()
    pd.testing.assert_series_equal(short, long.iloc[:23])
    building = "synthetic_verification_only"
    def reference(day, energy):
        start = pd.Timestamp(day)
        return {"start": start.isoformat(), "end": (start + pd.Timedelta(hours=6)).isoformat(),
                "block": 0, "weekend": 0, "temperature": 20., "temperature_24h": 20., "energy": energy}
    history = {building: [reference(day, 100.) for day in pd.bdate_range("2016-06-01", periods=20)]}
    initial = {building: [reference(day, 100.) for day in pd.bdate_range("2017-06-05", "2017-06-30")]}
    outcomes = {}
    for mode in ["enough", "insufficient", "conflict"]:
        recent = deepcopy(initial)
        if mode == "insufficient":
            recent[building] = []
        if mode == "conflict":
            for row in recent[building]:
                row["energy"] = 10000.
        engine = DiagnosticReplay(config, history, recent, {building: True})
        for day in ["2017-07-03", "2017-07-10", "2017-07-17"]:
            block = reference(day, 600.)
            iso = pd.Timestamp(day).isocalendar()
            block.update(iso_week=f"{iso.year}-W{iso.week:02d}", quality_complete=True, reference_complete=True,
                         good_hours=6, hourly_energy_kwh=[100.]*6, hourly_q20_kwh=[20.]*6, hourly_q80_kwh=[50.]*6)
            decision = engine.ingest(building, block)
        outcomes[mode] = decision
    assert outcomes["enough"]["status"] == "review_candidate"
    assert outcomes["conflict"]["status"] == "reference_conflict"
    assert outcomes["insufficient"]["status"] == "recent_support_insufficient"
    assert outcomes["insufficient"]["comparisons"]["recent_before_event"]["n"] == 2
    assert all(outcomes[m]["policy_eligibility"]["prefix_28d"] for m in outcomes)
    assert not outcomes["conflict"]["policy_eligibility"]["guarded_28d"]
    assert not outcomes["insufficient"]["policy_eligibility"]["guarded_28d"]
    return {"zero_prefix_not_rewritten": True, "supported_toy_case_admitted": True,
            "conflicting_toy_case_rejected": True, "insufficient_toy_case_rejected": True,
            "current_block_not_its_own_recent_reference": True,
            "note": "Toy values verify program gates only; not building performance data or savings evidence."}


def verify(config, frames, bases, sequences, choices, historic, initial, streams, gates, engine):
    toy = toy_gate_checks(config)
    cutoff = pd.Timestamp("2017-08-07T00:00:00")
    target_hour = pd.Timestamp("2017-08-07T12:00:00")
    hourly_checks, altered_streams = [], {}
    columns = ["q20", "q50", "q80"]
    for b, frame in frames.items():
        changed = frame.copy()
        changed.loc[target_hour, "y"] += 1000000
        method = choices[b]["model"]
        changed_sequence = reference_sequence(changed, bases[b], method, config)
        mask = sequences[b].index <= target_hour
        np.testing.assert_allclose(sequences[b].loc[mask, columns], changed_sequence.loc[mask, columns], equal_nan=True)
        changed = frame.copy()
        changed.loc[changed.index >= cutoff, "y"] += 1000000
        future_sequence = reference_sequence(changed, bases[b], method, config)
        before = sequences[b].index < cutoff
        np.testing.assert_allclose(sequences[b].loc[before, columns], future_sequence.loc[before, columns], equal_nan=True)
        pd.testing.assert_series_equal(sequences[b].loc[before, "good_online"], future_sequence.loc[before, "good_online"])
        prefix = reference_sequence(frame.loc[frame.index < cutoff], bases[b].loc[bases[b].index < cutoff], method, config)
        pd.testing.assert_frame_equal(sequences[b].loc[before], prefix)
        altered_streams[b] = [r for r in blocks_from_hours(future_sequence) if pd.Timestamp(r["start"]) >= pd.Timestamp(config["test"][0])]
        hourly_checks.append({"building": b, "current_target_mutation_invariant": True,
                              "future_target_mutation_invariant": True, "future_truncation_invariant": True})
    prefix_checks = []
    for stop in ["2017-07-03T00:00:00", "2017-08-07T00:00:00", "2017-10-09T12:00:00"]:
        replayed = replay(config, historic, initial, streams, gates, until=stop)
        old_records = [r for r in engine.records if r["decision_available_at"] <= stop]
        old_weeks = [r for r in engine.weekly_results if r["available_at"] <= stop]
        old_priorities = [r for r in engine.priorities if r["weekly_available_at"] <= stop]
        assert replayed.records == old_records
        assert replayed.weekly_results == old_weeks
        assert replayed.priorities == old_priorities
        prefix_checks.append({"through": stop, "decisions_identical": len(old_records), "weekly_records_identical": len(old_weeks)})
    altered = replay(config, historic, initial, altered_streams, gates)
    cutoff_text = cutoff.isoformat()
    assert [r for r in altered.records if r["decision_available_at"] <= cutoff_text] == [r for r in engine.records if r["decision_available_at"] <= cutoff_text]
    assert [r for r in altered.weekly_results if r["available_at"] <= cutoff_text] == [r for r in engine.weekly_results if r["available_at"] <= cutoff_text]
    assert [r for r in altered.priorities if r["weekly_available_at"] <= cutoff_text] == [r for r in engine.priorities if r["weekly_available_at"] <= cutoff_text]
    recent_checks, recurrence_checks = 0, 0
    for case in engine.records:
        assert case["decision_available_at"] == case["end"]
        if "recent_before_event" in case["comparisons"]:
            recent = case["comparisons"]["recent_before_event"]
            assert all(end <= case["start"] for end in recent.get("all_reference_ends", []))
            recent_checks += recent["n"]
        times = case["recurrence"]["recent_event_ends"]
        assert all(end <= case["end"] for end in times)
        assert all(pd.Timestamp(end) >= pd.Timestamp(case["end"]) - pd.Timedelta(days=config["candidate"]["recurrence_window_days"]) for end in times)
        recurrence_checks += len(times)
        assert case["human_review"]["confirmed_cause"] is None
        assert case["control_extension"]["verified_savings_kwh"] is None
    for week in engine.weekly_results:
        assert week["available_at"] == week["week_end"]
        assert pd.Timestamp(week["available_at"]).dayofweek == 0
        assert week["selected_cases"] <= config["weekly_slots"]
        if not week["complete_week"]:
            assert week["selected_cases"] == 0
    for row in engine.priorities:
        assert row["policy_eligibility"][row["policy"]]
        assert row["end"] <= row["weekly_available_at"]
    assert len(engine.records) == len({r["case_id"] for r in engine.records})
    return {"all_passed": True, "toy_gate_checks": toy, "hourly_time_checks": hourly_checks,
            "prefix_replay_checks": prefix_checks, "future_mutation_preserves_past_engine_outputs": True,
            "recent_reference_timing_checks": recent_checks, "recurrence_event_timing_checks": recurrence_checks,
            "unique_block_decisions": len(engine.records), "weekly_records_checked": len(engine.weekly_results),
            "weekly_small_pools_enumeration_checked": sum(r["exact_enumeration_checked"] for r in engine.weekly_results),
            "all_human_causes_and_savings_remain_unknown": True}


def fit_base(frame, profile, config):
    """Fit the frozen strategy on 2016; no test-driven selection or old artifacts."""
    train = frame.loc[(frame.index < config["train"][1]) & frame.good]
    evaluation = frame.loc[frame.index >= config["validation"][0]]
    method = profile["base_model"]
    if method == "calendar_baseline":
        values = clock_prediction(train, evaluation)
    elif method.startswith("weather_gb_leaf"):
        leaf = int(method.split("leaf")[-1])
        features = CALENDAR + WEATHER
        good = np.isfinite(evaluation[features]).all(axis=1)
        values = np.full((len(evaluation), 3), np.nan)
        values[good] = fit_predict(train, evaluation.loc[good], features, leaf,
                                   {**config, "quantiles": config["rolling"]["quantiles"]})
    else:
        raise ValueError(f"Unknown base profile: {method}")
    return pd.DataFrame(values, index=evaluation.index, columns=QUANTILE_COLUMNS)


def run(data_root, output, config):
    """Complete local workflow; all external data locations are explicit."""
    data_root, output = Path(data_root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    save(output / "configuration.json", config)
    frames, audit = prepare(config, output, data_root)
    bases, sequences, historic, initial, streams, choices = {}, {}, {}, {}, {}, {}
    for building, frame in frames.items():
        profile = config["profiles"][building]
        bases[building] = fit_base(frame, profile, config)
        choices[building] = {"model": profile["reference_method"],
                             "validation_reference_gate_passed": profile["validation_reference_gate_passed"]}
        sequence = reference_sequence(frame, bases[building], profile["reference_method"], config)
        sequences[building] = sequence
        sequence.to_csv(output / f"causal_hourly_reference_{building}.csv", index_label="local_timestamp")
        blocks = blocks_from_hours(sequence)
        start = pd.Timestamp(config["test"][0])
        historic[building] = historic_blocks(frame, config)
        initial[building] = [r for r in blocks if r["quality_complete"] and start - pd.Timedelta(days=29) <= pd.Timestamp(r["start"]) < start]
        streams[building] = [r for r in blocks if pd.Timestamp(r["start"]) >= start]
        print("Prepared independent profile", building, profile["reference_method"], flush=True)
    gates = {b: choices[b]["validation_reference_gate_passed"] for b in config["buildings"]}
    engine = replay(config, historic, initial, streams, gates)
    verification = verify(config, frames, bases, sequences, choices, historic, initial, streams, gates, engine)
    primary = [r for r in engine.priorities if r["policy"] == "guarded_28d"]
    summary = {"all_test_blocks": len(engine.records), "supported_model_excess_blocks": sum(r["model_excess_supported"] for r in engine.records),
               "primary_status_counts": dict(Counter(r["status"] for r in engine.records)),
               "primary_candidates": sum(r["policy_eligibility"]["guarded_28d"] for r in engine.records),
               "primary_weekly_priorities": len(primary), "scientific_rules_unchanged": True,
               "claim": "Evidence-based investigation only; no measured savings or device control."}
    for filename, content in [("all_block_decisions.json", engine.records), ("weekly_policy_comparison.json", engine.weekly_results),
                              ("all_policy_priorities.json", engine.priorities), ("primary_weekly_priorities.json", primary),
                              ("summary.json", summary), ("verification.json", verification)]:
        save(output / filename, content)
    after = {name: sha256(data_root / name) for name in audit["source_sha256"]}
    assert after == audit["source_sha256"]
    save(output / "run_manifest.json", {"python": platform.python_version(), "executable": sys.executable,
         "algorithm_sha256": sha256(Path(__file__)), "source_sha256": after, "source_files_unchanged": True,
         "packages": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scikit-learn"]},
         "self_checks_passed": verification["all_passed"], "elapsed_seconds": time.perf_counter() - started,
         "old_experiment_files_required": False, "embedded_profiles_selected_using": config["validation"]})
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Standalone building-energy diagnosis, with frozen V6 scientific settings")
    parser.add_argument("--data-root", type=Path, required=True, help="Folder containing dataset/raw/electricity.csv, dataset/weather.csv and dataset/metadata.csv")
    parser.add_argument("--output", type=Path, required=True, help="A NEW output directory; existing directories are not overwritten")
    parser.add_argument("--config", type=Path, help="Optional complete replacement config; no old experiment files needed")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else deepcopy(DEFAULT_CONFIG)
    run(args.data_root, args.output, config)


if __name__ == "__main__":
    main()

