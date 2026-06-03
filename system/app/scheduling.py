"""Scheduling helpers shared by API, chat tools, and the engine."""
from __future__ import annotations

import re
from typing import Any

from .clock import DAY_MS, now_ms

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
WEEK_MS = 7 * DAY_MS


def _number_from_text(text: str) -> tuple[re.Match[str] | None, float]:
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return match, float(match.group(1)) if match else 1.0


def cadence_interval_ms(cadence: str | None, default: int | None = None) -> int | None:
    """Return a supported cadence interval in milliseconds."""
    if cadence is None:
        return default
    text = str(cadence).strip().lower()
    if not text:
        return default

    cron_step = re.search(r"(?:^|\s)\*/(\d+(?:\.\d+)?)(?:\s|$)", text)
    if cron_step:
        minutes = float(cron_step.group(1))
        return int(minutes * MINUTE_MS) if minutes > 0 else default

    number_match, number = _number_from_text(text)
    if number <= 0:
        return default

    if "นาที" in text or re.search(r"\bminutes?\b|\bmins?\b", text) or re.search(r"\d+(?:\.\d+)?\s*m\b", text):
        return int(number * MINUTE_MS)
    if "ชั่วโมง" in text or "ชม" in text or re.search(r"\bhours?\b|\bhrs?\b|\bhourly\b", text) or re.search(r"\d+(?:\.\d+)?\s*h\b", text):
        return int(number * HOUR_MS)
    if "สัปดาห์" in text or re.search(r"\bweeks?\b|\bweekly\b", text):
        return int(number * WEEK_MS)
    if "วัน" in text or re.search(r"\bdays?\b|\bdaily\b", text):
        return int(number * DAY_MS)
    if number_match and number in {6.0, 12.0}:
        return int(number * HOUR_MS)
    return default


def next_run_for_cadence(cadence: str | None, one_shot_at: int | str | None = None, *, now: int | None = None) -> int | None:
    if one_shot_at is not None and str(one_shot_at).strip():
        try:
            return int(float(one_shot_at))
        except (TypeError, ValueError):
            return None
    if not cadence:
        return None
    interval = cadence_interval_ms(cadence, DAY_MS)
    if interval is None:
        return None
    return (now if now is not None else now_ms()) + interval


def infer_cadence_from_text(*texts: str | None) -> str | None:
    for text in texts:
        if text and cadence_interval_ms(str(text), None) is not None:
            return str(text).strip()
    return None


def cadence_from_schedule_object(schedule: Any) -> str | None:
    if isinstance(schedule, str):
        return schedule.strip() or None
    if not isinstance(schedule, dict):
        return None
    for key in ("cadence", "interval", "cron", "rrule"):
        value = schedule.get(key)
        if value:
            return str(value).strip()
    every = schedule.get("every") or schedule.get("value") or schedule.get("amount")
    unit = schedule.get("unit")
    if every and unit:
        return f"every {every} {unit}"
    return None


def resolve_trigger_cadence(cadence: str | None, *fallback_texts: str | None) -> str | None:
    if cadence and str(cadence).strip():
        return str(cadence).strip()
    return infer_cadence_from_text(*fallback_texts)


def repair_trigger_schedule(trigger: dict[str, Any], *, now: int | None = None) -> bool:
    if trigger.get("kind") != "cron":
        return False
    changed = False
    cadence = resolve_trigger_cadence(trigger.get("cadence"), trigger.get("title"))
    if cadence != trigger.get("cadence"):
        trigger["cadence"] = cadence
        changed = True
    if trigger.get("enabled") and trigger.get("nextRunAt") is None:
        next_run = next_run_for_cadence(cadence, trigger.get("oneShotAt"), now=now)
        if next_run is not None:
            trigger["nextRunAt"] = next_run
            changed = True
    return changed
