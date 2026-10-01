"""Trusted campaign month boundaries for source monthly physiology deadlines."""

from bisect import bisect_right

from pydantic import model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PhysiologyCalendar(Record):
    # Absolute game seconds; campaign authoring supplies its actual calendar.
    month_boundaries: tuple[int, ...]
    months_per_year: int = 12
    months_per_season: int = 3

    @model_validator(mode="after")
    def valid_boundaries(self) -> PhysiologyCalendar:
        if (
            len(self.month_boundaries) < 2
            or self.month_boundaries[0] < 0
            or any(
                b <= a or (b - a) % 86400 != 0
                for a, b in zip(self.month_boundaries, self.month_boundaries[1:], strict=False)
            )
            or self.months_per_year < 1
            or self.months_per_season < 1
        ):
            raise ValueError("Campaign calendar requires ordered month boundaries")
        return self

    def deadline(self, started: int, frequency: str) -> int:
        months = {"month": 1, "season": self.months_per_season, "year": self.months_per_year}
        index = bisect_right(self.month_boundaries, started) - 1
        target = index + months[frequency]
        if index < 0 or target + 1 >= len(self.month_boundaries):
            raise ValidationError("Campaign calendar does not cover the dependency deadline")
        offset = started - self.month_boundaries[index]
        # Keep day/time where possible; a shorter month ends on its final day.
        target_length = self.month_boundaries[target + 1] - self.month_boundaries[target]
        day, seconds = divmod(offset, 86400)
        final_day = max(0, (target_length - 1) // 86400)
        return self.month_boundaries[target] + min(day, final_day) * 86400 + seconds
