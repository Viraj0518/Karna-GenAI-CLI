"""Cron expression parsing and next-fire-time computation.

Supports:
- ``@hourly``, ``@daily``, ``@weekly``, ``@monthly``, ``@yearly`` shortcuts
- Classic 5-field cron: ``minute hour day-of-month month day-of-week``
- Ranges (``1-5``), lists (``1,3,5``), steps (``*/15``), wildcard (``*``)
- Day-of-week names: ``SUN MON TUE WED THU FRI SAT`` (case-insensitive)
- Month names: ``JAN FEB ... DEC`` (case-insensitive)

If ``croniter`` is installed we delegate to it for maximum correctness;
otherwise the minimal built-in parser covers the common cases listed
above. This keeps the cron feature dependency-free for simple use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:  # optional fast-path
    from croniter import croniter as _croniter  # type: ignore[import-not-found]

    _HAS_CRONITER = True
except Exception:  # pragma: no cover - exercised only when dep missing
    _HAS_CRONITER = False


_SHORTCUTS: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

_DOW_NAMES = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}

_MONTH_NAMES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


class CronParseError(ValueError):
    """Raised when a cron expression cannot be parsed."""

@dataclass
class CronSpec:
    """Parsed cron expression as 5 sets of allowed values."""

    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    # Day of week: 0 = Sunday through 6 = Saturday (classic cron).
    dows: set[int]
    raw: str

    def matches(self, dt: datetime) -> bool:
        """Return True if *dt* (to the minute) matches this spec."""
        if dt.minute not in self.minutes:
            return False
        if dt.hour not in self.hours:
            return False
        if dt.month not in self.months:
            return False
        # cron DOW: Sunday = 0; Python's weekday(): Monday = 0.
        dow = (dt.weekday() + 1) % 7
        # classic cron: DOM and DOW are OR'd when both restricted.
        dom_restricted = self.days != set(range(1, 32))
        dow_restricted = self.dows != set(range(0, 7))
        if dom_restricted and dow_restricted:
            if dt.day not in self.days and dow not in self.dows:
                return False
        else:
            if dt.day not in self.days:
                return False
            if dow not in self.dows:
                return False
        return True


def _expand_field(
    field: str,
    lo: int,
    hi: int,
    *,
    names: dict[str, int] | None = None,
) -> set[int]:
    """Expand a single cron field into a set of ints in [lo, hi]."""
    result: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronParseError(f"empty sub-field in {field!r}")

        step = 1
        if "/" in part:
            range_part, _, step_str = part.partition("/")
            try:
                step = int(step_str)
            except ValueError as exc:
                raise CronParseError(f"bad step in {part!r}") from exc
            if step <= 0:
                raise CronParseError(f"step must be positive in {part!r}")
        else:
            range_part = part

        if range_part == "*":
            start, end = lo, hi
        elif "-" in range_part:
            a, _, b = range_part.partition("-")
            start = _lookup(a.strip(), names, lo, hi)
            end = _lookup(b.strip(), names, lo, hi)
        else:
            start = end = _lookup(range_part.strip(), names, lo, hi)

        if start < lo or end > hi or start > end:
            raise CronParseError(f"{range_part!r} out of range [{lo},{hi}]")

        for v in range(start, end + 1, step):
            result.add(v)

    return result


def _lookup(token: str, names: dict[str, int] | None, lo: int, hi: int) -> int:
    """Look up a token as a name or int."""
    if names and token.upper() in names:
        return names[token.upper()]
    try:
        v = int(token)
    except ValueError as exc:
        raise CronParseError(f"unrecognized token {token!r}") from exc
    if v < lo or v > hi:
        raise CronParseError(f"{v} out of range [{lo},{hi}]")
    return v


def parse_expression(expr: str) -> CronSpec:
    """Parse a cron expression into a :class:`CronSpec`.

    Accepts shortcuts (``@daily``) and 5-field syntax.
    """
    if not expr or not expr.strip():
        raise CronParseError("empty cron expression")

    raw = expr.strip()
    if raw.startswith("@"):
        expanded = _SHORTCUTS.get(raw.lower())
        if expanded is None:
            raise CronParseError(f"unknown shortcut {raw!r}")
        expr = expanded
    else:
        expr = raw

    fields = expr.split()
    if len(fields) != 5:
        raise CronParseError(f"expected 5 fields, got {len(fields)}: {expr!r}")

    minute, hour, dom, month, dow = fields
    return CronSpec(
        minutes=_expand_field(minute, 0, 59),
        hours=_expand_field(hour, 0, 23),
        days=_expand_field(dom, 1, 31),
        months=_expand_field(month, 1, 12, names=_MONTH_NAMES),
        # Accept both 0 and 7 for Sunday, normalize to 0.
        dows={v % 7 for v in _expand_field(dow, 0, 7, names=_DOW_NAMES)},
        raw=raw,
    )


def next_fire_time(
    expr: str,
    *,
    after: datetime | None = None,
    max_iterations: int = 366 * 24 * 60,
) -> datetime:
    """Return the next datetime (UTC, minute resolution) the expression fires.

    If ``croniter`` is available, we use it for correctness. Otherwise we
    scan minute-by-minute up to *max_iterations* (one year). All datetimes
    are UTC.
    """
    after = after or datetime.now(timezone.utc)
    # Normalize to minute resolution and advance one minute — cron fires
    # "on the minute", and "next after now" must be strictly > now.
    after = after.replace(second=0, microsecond=0)

    if _HAS_CRONITER:
        # croniter accepts shortcuts directly.
        it = _croniter(expr, after)
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt

    spec = parse_expression(expr)
    candidate = after + timedelta(minutes=1)
    for _ in range(max_iterations):
        if spec.matches(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise CronParseError(f"no firing within {max_iterations} minutes for {expr!r}")


def is_due(
    expr: str,
    last_run_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if a job with this expression should fire now.

    "Due" means: the next fire time computed from ``last_run_at``
    (or from one period ago if None) is <= now.
    """
    now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    if last_run_at is None:
        # A brand-new job fires on first tick whenever the expression
        # is valid — otherwise users would have to wait up to a day.
        return True
    anchor = last_run_at.replace(second=0, microsecond=0)
    nxt = next_fire_time(expr, after=anchor)
    return nxt <= now
