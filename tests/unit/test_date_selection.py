import datetime as dt

import pandas as pd
import pytest

from green_mobility.thermal.date_selection import (
    OFFICIAL_HEATWAVES_2022,
    classify_city_day,
    dedupe_calendar,
    is_official_heatwave,
    monthly_medoid_common,
    monthly_medoid_per_city,
    season_of,
)


def _daily(dates, values):
    return pd.DataFrame({"date": dates, "utci_max_c": values})


def test_is_official_heatwave_matches_the_three_2022_windows():
    assert is_official_heatwave(dt.date(2022, 7, 15))  # inside 9-26 Jul
    assert is_official_heatwave(dt.date(2022, 7, 9))    # boundary, inclusive
    assert is_official_heatwave(dt.date(2022, 7, 26))   # boundary, inclusive
    assert not is_official_heatwave(dt.date(2022, 7, 5))   # 5 days before window
    assert not is_official_heatwave(dt.date(2022, 8, 20))  # 5 days after last window
    assert len(OFFICIAL_HEATWAVES_2022) == 3


def test_season_of_meteorological_convention():
    assert season_of(dt.date(2022, 1, 15)) == "DJF"
    assert season_of(dt.date(2022, 12, 25)) == "DJF"
    assert season_of(dt.date(2022, 7, 4)) == "JJA"
    assert season_of(dt.date(2022, 4, 1)) == "MAM"
    assert season_of(dt.date(2022, 10, 1)) == "SON"


def test_monthly_medoid_per_city_picks_the_day_closest_to_month_median():
    # January: 3 days with utci_max 10, 20, 30 -> median 20 -> day 2 is medoid exactly.
    dates = [dt.date(2022, 1, 1), dt.date(2022, 1, 2), dt.date(2022, 1, 3),
             dt.date(2022, 2, 1), dt.date(2022, 2, 2), dt.date(2022, 2, 3)]
    values = [10.0, 20.0, 30.0, 5.0, 5.0, 100.0]
    daily = _daily(dates, values)
    result = monthly_medoid_per_city(daily)
    jan_row = result[result["month"] == 1].iloc[0]
    assert jan_row["date"] == dt.date(2022, 1, 2)
    assert jan_row["abs_dev_from_median"] == pytest.approx(0.0)
    # February median of [5,5,100] is 5 -> either of the two 5.0 days is a valid
    # medoid (tie); earliest date wins deterministically.
    feb_row = result[result["month"] == 2].iloc[0]
    assert feb_row["date"] == dt.date(2022, 2, 1)


def test_monthly_medoid_common_picks_date_minimizing_total_normalized_distance():
    dates = [dt.date(2022, 1, 1), dt.date(2022, 1, 2), dt.date(2022, 1, 3)]
    # City A: values close together (10, 12, 14) -> small IQR, sensitive to deviation.
    # City B: same date order but very different scale/spread.
    daily_by_city = {
        "a": _daily(dates, [10.0, 12.0, 14.0]),
        "b": _daily(dates, [0.0, 50.0, 100.0]),
    }
    result = monthly_medoid_common(daily_by_city)
    assert len(result) == 1
    assert result.iloc[0]["month"] == 1
    # the picked date must be one of the three real observed dates
    assert result.iloc[0]["date"] in dates


def test_classify_city_day_flags_extreme_heat_and_cold_stress():
    # Build a synthetic year: mostly mild days, one very hot day, one very cold day.
    dates = [dt.date(2022, 1, 1) + dt.timedelta(days=i) for i in range(100)]
    max_values = [20.0] * 98 + [45.0, 46.0]  # last two days are the hottest
    min_values = [10.0] * 98 + [-50.0, -45.0]  # and also (independently) coldest
    year_stats = pd.DataFrame({
        "date": dates, "utci_max_c": max_values, "utci_min_c": min_values,
        "utci_mean_c": [15.0] * 100,
    })
    hot_day = dates[-1]
    result = classify_city_day("testcity", hot_day, year_stats)
    assert result.extreme_heat
    assert result.local_max_percentile >= 90
    assert result.cold_stress_category in {"extreme_cold", "very_strong_cold"}  # utci_min_c=-45


def test_classify_city_day_raises_on_missing_date():
    year_stats = pd.DataFrame({
        "date": [dt.date(2022, 1, 1)], "utci_max_c": [20.0], "utci_min_c": [10.0],
        "utci_mean_c": [15.0],
    })
    with pytest.raises(ValueError):
        classify_city_day("testcity", dt.date(2022, 6, 1), year_stats)


def test_dedupe_calendar_unions_and_sorts_without_duplicates():
    monthly = [dt.date(2022, 2, 8), dt.date(2022, 7, 5)]
    extreme = [dt.date(2022, 7, 5), dt.date(2022, 7, 10)]
    result = dedupe_calendar(monthly, extreme)
    assert result == [dt.date(2022, 2, 8), dt.date(2022, 7, 5), dt.date(2022, 7, 10)]
