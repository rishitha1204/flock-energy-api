import pytest

from app.errors import InvalidRequestError, MeterNotFoundError, PortalUnexpectedResponseError
from app.service import clean_text, parse_energy_timestamp, parse_iso_date, to_float


def test_to_float_converts_strings():
    assert to_float("48438.74") == 48438.74
    assert to_float(" 226 ") == 226.0
    assert to_float("") is None
    assert to_float(None) is None


@pytest.mark.parametrize("bad", ["abc", "nan", "inf"])
def test_to_float_rejects_garbage(bad):
    with pytest.raises(PortalUnexpectedResponseError):
        to_float(bad)


def test_clean_text():
    assert clean_text("  HPL \n") == "HPL"
    assert clean_text("   ") is None


def test_timestamp_is_day_first():
    ts = parse_energy_timestamp("23/06/2026 23:30")
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2026, 6, 23, 23, 30)


def test_bad_timestamp_is_unexpected():
    with pytest.raises(PortalUnexpectedResponseError):
        parse_energy_timestamp("2026-06-23T23:30")


@pytest.mark.parametrize("bad", ["invalid-date", "2026-13-01", "2026-02-30", "01/06/2026", "2026-6-1", "20260601"])
def test_bad_dates_rejected(bad):
    with pytest.raises(InvalidRequestError):
        parse_iso_date(bad, "from")


def test_good_date_and_empty():
    assert parse_iso_date("2026-06-01", "from").isoformat() == "2026-06-01"
    assert parse_iso_date(None, "from") is None


def test_list_meters(service):
    result = service.list_meters("", 1)
    assert result.pagination.total == 3
    assert result.pagination.page_size == 20
    assert result.pagination.total_pages == 1
    assert result.data[0].meter_id == "J100000"
    assert result.data[0].install_status == "Decommissioned"


def test_empty_search(service):
    result = service.list_meters("ZZZ999", 1)
    assert result.data == []
    assert result.pagination.total == 0
    assert result.pagination.total_pages == 0


def test_get_meter_with_location(service):
    meter = service.get_meter("J100000")
    assert meter.serial_no == "SE33962"
    assert meter.location.latitude == pytest.approx(26.938961002479868)
    assert meter.location.longitude == pytest.approx(75.83095696146852)


def test_get_meter_unknown(service):
    with pytest.raises(MeterNotFoundError):
        service.get_meter("ZZZ999")


def test_consumption_normalised(service):
    result = service.get_consumption("J100000", "2026-06-01", "2026-06-01")
    assert result.count == 48
    first = result.data[0]
    assert first.timestamp.isoformat() == "2026-06-01T00:00:00"
    assert isinstance(first.kwh, float) and first.volt_r == 226.0
    assert result.data == sorted(result.data, key=lambda r: r.timestamp)


def test_consumption_reversed_range(service):
    with pytest.raises(InvalidRequestError):
        service.get_consumption("J100000", "2026-06-10", "2026-06-01")


def test_consumption_bad_row_is_unexpected(service, fake_portal):
    fake_portal.energy_rows = [{"timestamp": "not a date", "kwh": "1", "kvah": "1", "voltR": "1"}]
    with pytest.raises(PortalUnexpectedResponseError):
        service.get_consumption("J100000", None, None)
