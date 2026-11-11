"""Field validation for website monitors (and the name rule customers and groups share).

Each cleaner returns the normalised value or raises InvalidMonitor with a sentence
the API returns as its ``detail``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..core.alert_models import ALLOWED_ALERT_DELAY_MINUTES, ALLOWED_REMINDER_MINUTES
from ..core.uptime_models import ALLOWED_INTERVAL_SECONDS, MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS
from .errors import InvalidMonitor

# url -> normalised url; raises InvalidMonitor / BlockedTarget (see url_policy).
UrlCheck = Callable[[str], str]

MAX_NAME_LENGTH = 200
MAX_KEYWORD_LENGTH = 200
EDITABLE_FIELDS = (
    "name", "url", "interval_seconds", "timeout_seconds", "expected_status",
    "keyword", "group_id", "customer_id",
    "alerts_enabled", "reminder_minutes", "alert_delay_minutes",
)


def has_control_characters(text: str) -> bool:
    """True if ``text`` holds a newline, tab or other control character (ord < 32 or
    127) — never allowed in names, which end up in email subjects."""
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _clean_name(value: Any) -> str:
    name = (value or "").strip() if isinstance(value, str) else ""
    if not name:
        raise InvalidMonitor("name must not be blank")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidMonitor(f"name must be at most {MAX_NAME_LENGTH} characters")
    if has_control_characters(name):
        raise InvalidMonitor("name must not contain line breaks or control characters")
    return name


def _clean_interval(value: Any) -> int:
    if value not in ALLOWED_INTERVAL_SECONDS:
        allowed = ", ".join(str(v) for v in ALLOWED_INTERVAL_SECONDS)
        raise InvalidMonitor(f"interval_seconds must be one of {allowed}")
    return value


def _clean_timeout(value: Any) -> int:
    if not isinstance(value, int) or not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise InvalidMonitor(
            f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _clean_expected_status(value: Any) -> int:
    if not isinstance(value, int) or not 100 <= value <= 599:
        raise InvalidMonitor("expected_status must be between 100 and 599")
    return value


def _clean_keyword(value: Any) -> str | None:
    """Trimmed keyword; blank means "no keyword"."""
    value = value.strip() if isinstance(value, str) else value
    if not value:
        return None
    if len(value) > MAX_KEYWORD_LENGTH:
        raise InvalidMonitor(f"keyword must be at most {MAX_KEYWORD_LENGTH} characters")
    return value


def _clean_alerts_enabled(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidMonitor("alerts_enabled must be true or false")
    return value


def _one_of_or_default(field: str, allowed: tuple[int, ...]) -> Callable[[Any], int | None]:
    """A cleaner accepting null (= the global default) or one of ``allowed``."""

    def clean(value: Any) -> int | None:
        if value is not None and (isinstance(value, bool) or value not in allowed):
            raise InvalidMonitor(f"{field} must be null or one of {', '.join(map(str, allowed))}")
        return value

    return clean


def _same(value: Any) -> Any:
    return value


_CLEANERS: dict[str, Callable[[Any], Any]] = {
    "name": _clean_name,
    "interval_seconds": _clean_interval,
    "timeout_seconds": _clean_timeout,
    "expected_status": _clean_expected_status,
    "keyword": _clean_keyword,
    "group_id": _same,
    "customer_id": _same,
    "alerts_enabled": _clean_alerts_enabled,
    "reminder_minutes": _one_of_or_default("reminder_minutes", ALLOWED_REMINDER_MINUTES),
    "alert_delay_minutes": _one_of_or_default("alert_delay_minutes", ALLOWED_ALERT_DELAY_MINUTES),
}


def clean_fields(fields: Mapping[str, Any], check_url: UrlCheck) -> dict[str, Any]:
    """Validate and normalise the given monitor fields (unknown fields are an error)."""
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise InvalidMonitor(f"unknown field(s): {', '.join(sorted(unknown))}")
    cleaned = {k: _CLEANERS[k](v) for k, v in fields.items() if k != "url"}
    if "url" in fields:
        cleaned["url"] = check_url(fields["url"])
    return cleaned
