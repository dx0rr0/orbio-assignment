import pytest

from claims_agent.domain.errors import FieldError
from claims_agent.domain.identifiers import (
    normalize_other_plate,
    normalize_plate,
    normalize_policy_number,
)


@pytest.mark.parametrize("raw", ["AUT-12345678", "aut 1234 5678", "AUT12345678", " Aut-1234-5678 "])
def test_policy_number_is_normalized_from_spoken_variants(raw):
    assert normalize_policy_number(raw) == "AUT-12345678"


@pytest.mark.parametrize("raw", ["AUT-1234567", "HOG-12345678", "12345678", "", None, 12345678])
def test_malformed_policy_number_is_rejected(raw):
    with pytest.raises(FieldError):
        normalize_policy_number(raw)


@pytest.mark.parametrize("raw", ["1234 BCD", "1234-bcd", "1234BCD"])
def test_plate_is_normalized(raw):
    assert normalize_plate(raw) == "1234BCD"


@pytest.mark.parametrize("raw", ["1234 ABC", "123 BCD", "1234 BCQ", "V-1234-AB"])
def test_plate_outside_current_spanish_format_is_rejected(raw):
    with pytest.raises(FieldError):
        normalize_plate(raw)


def test_other_vehicle_plate_accepts_older_formats():
    assert normalize_other_plate("V-1234-AB") == "V1234AB"


def test_other_vehicle_plate_rejects_nonsense():
    with pytest.raises(FieldError):
        normalize_other_plate("no sé")
