"""Sequential diagnostic state; each immutable decision uses completed blocks only."""
from collections import defaultdict
from copy import deepcopy
import itertools

import numpy as np
import pandas as pd

POLICIES = ["prefix_cumulative", "prefix_28d", "guarded_28d"]
STATUS_NAMES = {
    "data_unavailable": "数据不足", "reference_unavailable": "模型参照不足",
    "model_gate_failed": "验证门槛未通过", "no_model_excess": "未达到模型偏高条件",
    "historic_support_insufficient": "去年参照不足", "reference_conflict": "参照存在分歧",
    "recent_support_insufficient": "近期参照不足", "recurrence_insufficient": "尚未重复够",
    "evidence_intermediate": "两期偏高证据不足", "review_candidate": "进入核查候选",
}


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
