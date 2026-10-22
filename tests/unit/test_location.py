import pytest

from claims_agent.domain.errors import FieldError
from claims_agent.domain.location import parse_loss_location
from claims_agent.domain.provinces import PROVINCES, match_province


def test_there_are_52_provinces():
    assert len(PROVINCES) == 52
    assert len(set(PROVINCES)) == 52


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("valencia", "Valencia"),
        ("València", "Valencia"),
        ("CADIZ", "Cádiz"),
        ("Gerona", "Girona"),
        ("Vizcaya", "Bizkaia"),
        ("Islas Baleares", "Baleares"),
    ],
)
def test_province_matches_ignoring_case_accents_and_language(raw, expected):
    assert match_province(raw) == expected


def test_unknown_province_does_not_match():
    assert match_province("Comunidad Valenciana") is None


def test_location_in_spain_gets_canonical_province_and_country():
    location = parse_loss_location(
        {"municipality": "Valencia", "province": "valència", "country": "Spain"}
    )
    assert location.province == "Valencia"
    assert location.country == "España"
    assert location.way is None
    assert location.in_spain


def test_the_way_is_kept_trimmed():
    location = parse_loss_location(
        {
            "way": " A-3, km 22 ",
            "municipality": "Arganda del Rey",
            "province": "Madrid",
            "country": "España",
        }
    )
    assert location.way == "A-3, km 22"


def test_location_with_an_invalid_spanish_province_is_rejected():
    with pytest.raises(FieldError, match="not a Spanish province"):
        parse_loss_location({"municipality": "Valencia", "province": "Levante", "country": "ES"})


@pytest.mark.parametrize(
    "country",
    [
        "Spain",
        "España",
        "ESPAÑA",
        " españa ",
        "Espanya",
        "Espainia",
        "ES",
        "Reino de España",
        # Regions some declarants give as their country.
        "Catalunya",
        "Euskadi",
    ],
)
def test_spain_is_recognised_in_its_usual_spellings(country):
    location = parse_loss_location(
        {"municipality": "Girona", "province": "Girona", "country": country}
    )
    assert location.country == "España"
    assert location.in_spain


def test_location_abroad_parses_but_is_not_in_spain():
    location = parse_loss_location(
        {"municipality": "Oporto", "province": "Porto", "country": "Portugal"}
    )
    assert not location.in_spain


@pytest.mark.parametrize("missing", ["municipality", "province", "country"])
def test_location_requires_municipality_province_and_country(missing):
    raw = {"municipality": "Sevilla", "province": "Sevilla", "country": "España"}
    raw[missing] = " "
    with pytest.raises(FieldError, match=missing):
        parse_loss_location(raw)
