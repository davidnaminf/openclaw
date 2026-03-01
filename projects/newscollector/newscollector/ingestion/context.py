from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List

import httpx

OPEN_METEO_API_BASE = "https://api.open-meteo.com/v1/forecast"
NAGER_DATE_API_BASE = "https://date.nager.at/api/v3"

WMO_WEATHER_CODE_TEXT = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


@dataclass
class WeatherSnapshot:
    location_name: str
    forecast_date: date
    weather_code: int | None
    weather_text: str
    temp_min_c: float | None
    temp_max_c: float | None
    precipitation_probability_max: float | None
    precipitation_sum_mm: float | None
    source_url: str


@dataclass
class HolidayEvent:
    country_code: str
    date: date
    local_name: str
    name: str
    types: List[str] = field(default_factory=list)


@dataclass
class HolidayWindow:
    start_date: date
    end_date: date
    by_country: Dict[str, List[HolidayEvent]] = field(default_factory=dict)


def _pick_from_series(series: object, index: int) -> object | None:
    if isinstance(series, list) and 0 <= index < len(series):
        return series[index]
    return None


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_weather_snapshot(
    location_name: str,
    latitude: float,
    longitude: float,
    timezone: str,
    forecast_days: int = 1,
    timeout_seconds: int = 20,
) -> tuple[WeatherSnapshot | None, str | None]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "forecast_days": max(1, min(16, int(forecast_days))),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum"
        ),
    }

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(OPEN_METEO_API_BASE, params=params)
            source_url = str(response.request.url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # pragma: no cover - network failures
        return None, f"Weather fetch failed: {exc}"

    daily = payload.get("daily", {})
    if not isinstance(daily, dict):
        return None, "Weather fetch failed: unexpected response format."

    times = daily.get("time", [])
    if not isinstance(times, list) or not times:
        return None, "Weather fetch failed: no forecast rows."

    forecast_date_raw = _pick_from_series(times, 0)
    try:
        forecast_date = date.fromisoformat(str(forecast_date_raw))
    except (TypeError, ValueError):
        return None, "Weather fetch failed: invalid forecast date."

    weather_code = _to_int(_pick_from_series(daily.get("weather_code"), 0))
    weather_text = (
        WMO_WEATHER_CODE_TEXT.get(weather_code, f"WMO code {weather_code}")
        if weather_code is not None
        else "Unknown"
    )

    snapshot = WeatherSnapshot(
        location_name=location_name,
        forecast_date=forecast_date,
        weather_code=weather_code,
        weather_text=weather_text,
        temp_min_c=_to_float(_pick_from_series(daily.get("temperature_2m_min"), 0)),
        temp_max_c=_to_float(_pick_from_series(daily.get("temperature_2m_max"), 0)),
        precipitation_probability_max=_to_float(
            _pick_from_series(daily.get("precipitation_probability_max"), 0)
        ),
        precipitation_sum_mm=_to_float(
            _pick_from_series(daily.get("precipitation_sum"), 0)
        ),
        source_url=source_url,
    )
    return snapshot, None


def _fetch_holidays_for_year(
    client: httpx.Client,
    country_code: str,
    year: int,
) -> tuple[List[HolidayEvent], str | None]:
    try:
        response = client.get(
            f"{NAGER_DATE_API_BASE}/PublicHolidays/{year}/{country_code}"
        )
        if response.status_code == 404:
            return [], None
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # pragma: no cover - network failures
        return [], f"{country_code}-{year}: {exc}"

    if not isinstance(payload, list):
        return [], f"{country_code}-{year}: unexpected response format."

    events: List[HolidayEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        date_raw = str(item.get("date", "")).strip()
        if not date_raw:
            continue
        try:
            event_date = date.fromisoformat(date_raw)
        except ValueError:
            continue
        events.append(
            HolidayEvent(
                country_code=country_code,
                date=event_date,
                local_name=str(item.get("localName", "")).strip(),
                name=str(item.get("name", "")).strip(),
                types=[
                    str(x).strip()
                    for x in item.get("types", [])
                    if str(x).strip()
                ]
                if isinstance(item.get("types"), list)
                else [],
            )
        )
    return events, None


def _expand_manual_holiday_ranges(
    country_code: str,
    rows: List[Dict[str, str]],
) -> List[HolidayEvent]:
    events: List[HolidayEvent] = []
    for row in rows:
        start_raw = str(row.get("start", "")).strip()
        end_raw = str(row.get("end", "")).strip() or start_raw
        name = str(row.get("name", "")).strip()
        local_name = str(row.get("local_name", "")).strip()
        if not start_raw:
            continue
        try:
            start_date = date.fromisoformat(start_raw)
            end_date = date.fromisoformat(end_raw)
        except ValueError:
            continue
        if end_date < start_date:
            continue
        title = name or local_name
        if not title:
            continue

        current = start_date
        while current <= end_date:
            events.append(
                HolidayEvent(
                    country_code=country_code,
                    date=current,
                    local_name=local_name,
                    name=title,
                    types=["Public", "ManualOverride"],
                )
            )
            current += timedelta(days=1)
    return events


def fetch_upcoming_holidays(
    country_codes: List[str],
    window_days: int = 7,
    reference_date: date | None = None,
    manual_ranges: Dict[str, List[Dict[str, str]]] | None = None,
    timeout_seconds: int = 20,
) -> tuple[HolidayWindow, str | None]:
    start_date = reference_date or date.today()
    end_date = start_date + timedelta(days=max(0, int(window_days)))
    years = sorted({start_date.year, end_date.year})

    by_country: Dict[str, List[HolidayEvent]] = {}
    errors: List[str] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for code in country_codes:
            country = code.upper().strip()
            if not country:
                continue
            all_events: List[HolidayEvent] = []
            for year in years:
                events, error = _fetch_holidays_for_year(client, country, year)
                if error:
                    errors.append(error)
                all_events.extend(events)
            all_events.extend(
                _expand_manual_holiday_ranges(
                    country_code=country,
                    rows=(manual_ranges or {}).get(country, []),
                )
            )

            filtered = [
                event
                for event in all_events
                if start_date <= event.date <= end_date
            ]
            filtered.sort(key=lambda x: (x.date, x.name, x.local_name))

            deduped: List[HolidayEvent] = []
            seen: set[tuple[date, str, str]] = set()
            for event in filtered:
                key = (
                    event.date,
                    event.name.strip().lower(),
                    event.local_name.strip().lower(),
                )
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(event)
            by_country[country] = deduped

    window = HolidayWindow(
        start_date=start_date,
        end_date=end_date,
        by_country=by_country,
    )
    error_text = "; ".join(errors) if errors else None
    return window, error_text
