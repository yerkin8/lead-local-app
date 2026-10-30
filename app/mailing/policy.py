"""Sending rules: daily limits with warm-up, hourly cap, random pauses, recipient time window."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Moscow"

# City (casefold, ё->е) -> IANA zone. Unknown cities fall back to the country, then Moscow.
CITY_TZ = {
    "калининград": "Europe/Kaliningrad",
    "самара": "Europe/Samara", "тольятти": "Europe/Samara", "ижевск": "Europe/Samara",
    "ульяновск": "Europe/Ulyanovsk", "саратов": "Europe/Saratov", "астрахань": "Europe/Astrakhan",
    "волгоград": "Europe/Volgograd",
    "екатеринбург": "Asia/Yekaterinburg", "челябинск": "Asia/Yekaterinburg", "уфа": "Asia/Yekaterinburg",
    "пермь": "Asia/Yekaterinburg", "тюмень": "Asia/Yekaterinburg", "оренбург": "Asia/Yekaterinburg",
    "курган": "Asia/Yekaterinburg", "магнитогорск": "Asia/Yekaterinburg", "сургут": "Asia/Yekaterinburg",
    "омск": "Asia/Omsk",
    "новосибирск": "Asia/Novosibirsk", "барнаул": "Asia/Barnaul", "томск": "Asia/Tomsk",
    "кемерово": "Asia/Novokuznetsk", "новокузнецк": "Asia/Novokuznetsk",
    "красноярск": "Asia/Krasnoyarsk", "абакан": "Asia/Krasnoyarsk",
    "иркутск": "Asia/Irkutsk", "улан-удэ": "Asia/Irkutsk",
    "чита": "Asia/Chita", "якутск": "Asia/Yakutsk", "благовещенск": "Asia/Yakutsk",
    "владивосток": "Asia/Vladivostok", "хабаровск": "Asia/Vladivostok",
    "магадан": "Asia/Magadan", "южно-сахалинск": "Asia/Sakhalin",
    "петропавловск-камчатский": "Asia/Kamchatka",
    "алматы": "Asia/Almaty", "астана": "Asia/Almaty", "шымкент": "Asia/Almaty",
    "караганда": "Asia/Almaty", "актобе": "Asia/Aqtobe", "атырау": "Asia/Atyrau",
}
COUNTRY_TZ = {
    "россия": DEFAULT_TZ, "беларусь": "Europe/Minsk", "казахстан": "Asia/Almaty",
    "армения": "Asia/Yerevan", "азербайджан": "Asia/Baku", "кыргызстан": "Asia/Bishkek",
    "молдова": "Europe/Chisinau", "таджикистан": "Asia/Dushanbe", "узбекистан": "Asia/Tashkent",
}


def _key(value: str | None) -> str:
    return str(value or "").casefold().replace("ё", "е").strip()


def recipient_tz(city: str | None, country: str | None) -> str:
    return CITY_TZ.get(_key(city)) or COUNTRY_TZ.get(_key(country)) or DEFAULT_TZ


def zone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:  # tzdata missing: Moscow has no DST, a fixed offset is exact
        return timezone(timedelta(hours=3))


@dataclass
class Policy:
    first_day_limit: int = 5
    daily_increment: int = 3
    daily_cap: int = 25
    hourly_cap: int = 8
    delay_min: int = 90
    delay_max: int = 240
    window_start: int = 10   # hour, recipient local time
    window_end: int = 19
    weekdays_only: bool = True
    flood_wait_max: int = 300  # longer waits pause the account until the end of the day

    LIMITS = {"first_day_limit": (1, 50), "daily_increment": (0, 10), "daily_cap": (1, 50),
              "hourly_cap": (1, 30), "delay_min": (45, 3600), "delay_max": (45, 7200),
              "window_start": (0, 23), "window_end": (1, 24), "flood_wait_max": (30, 3600)}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Policy":
        policy = cls()
        for key, (low, high) in cls.LIMITS.items():
            if data and key in data:
                value = int(data[key])
                if not low <= value <= high:
                    raise ValueError(f"Значение «{key}» должно быть от {low} до {high}")
                setattr(policy, key, value)
        if data and "weekdays_only" in data:
            policy.weekdays_only = bool(data["weekdays_only"])
        if policy.delay_max < policy.delay_min:
            raise ValueError("Максимальная пауза меньше минимальной")
        if policy.window_end <= policy.window_start:
            raise ValueError("Окно отправки задано неверно")
        if policy.first_day_limit > policy.daily_cap:
            raise ValueError("Лимит первого дня больше дневного потолка")
        return policy

    def to_dict(self) -> dict:
        return asdict(self)

    def daily_limit(self, first_send_day: date | None, today: date) -> int:
        if first_send_day is None:
            return self.first_day_limit
        days = max(0, (today - first_send_day).days)
        return min(self.daily_cap, self.first_day_limit + self.daily_increment * days)

    def pause_seconds(self, rng: random.Random | None = None) -> int:
        return (rng or random).randint(self.delay_min, self.delay_max)

    def next_window_start(self, moment: datetime, tz_name: str) -> datetime:
        """`moment` if it is inside the recipient window, otherwise the next window opening (UTC)."""
        tz = zone(tz_name)
        local = moment.astimezone(tz)
        for offset in range(0, 9):
            day = (local + timedelta(days=offset)).date()
            if self.weekdays_only and day.weekday() >= 5:
                continue
            opens = datetime.combine(day, time(self.window_start), tz)
            closes = datetime.combine(day, time(0), tz) + timedelta(hours=self.window_end)
            if local < opens:
                return opens.astimezone(timezone.utc)
            if local < closes:
                return moment.astimezone(timezone.utc)
        return (local + timedelta(days=1)).astimezone(timezone.utc)

    def end_of_day(self, moment: datetime, tz_name: str = DEFAULT_TZ) -> datetime:
        tz = zone(tz_name)
        local = moment.astimezone(tz)
        return datetime.combine(local.date() + timedelta(days=1), time(0), tz).astimezone(timezone.utc)
