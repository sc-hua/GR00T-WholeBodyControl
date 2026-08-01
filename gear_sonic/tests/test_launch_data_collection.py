from datetime import datetime

from gear_sonic.scripts.launch_data_collection import _timestamped_dataset_name


def test_dataset_name_appends_compact_datetime_suffix():
    now = datetime(2026, 8, 1, 15, 30, 45)

    assert (
        _timestamped_dataset_name("pico_bottle_to_bin", now)
        == "pico_bottle_to_bin_20260801_153045"
    )


def test_dataset_name_avoids_duplicate_separator_and_supports_empty_prefix():
    now = datetime(2026, 8, 1, 5, 6, 7)

    assert _timestamped_dataset_name("session-", now) == "session_20260801_050607"
    assert _timestamped_dataset_name("", now) == "20260801_050607"
