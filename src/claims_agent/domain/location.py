"""Where the loss happened, normalized to way / municipality / province / country."""

import unicodedata

from pydantic import BaseModel, ConfigDict

from claims_agent.domain.errors import FieldError
from claims_agent.domain.provinces import match_province

SPAIN = "España"
_SPAIN_NAMES = {"espana", "spain", "es", "esp"}


class LossLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    way: str | None
    municipality: str
    province: str
    country: str

    @property
    def in_spain(self) -> bool:
        return self.country == SPAIN


def _is_spain(country: str) -> bool:
    decomposed = unicodedata.normalize("NFKD", country.strip().casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)) in _SPAIN_NAMES


def _text(raw: dict, key: str, *, required: bool) -> str | None:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise FieldError(f"{key} is required")
        return None
    if not isinstance(value, str):
        raise FieldError(f"{key} must be a string")
    return value.strip()


def parse_loss_location(raw: object) -> LossLocation:
    """Parse `{"way"?, "municipality", "province", "country"}`.

    A location outside Spain parses fine (with its province unchecked): being abroad is not an
    input error but a reason the claim is not covered, which the claim lifecycle decides.
    """
    if not isinstance(raw, dict):
        raise FieldError("must be an object with way, municipality, province and country")
    way = _text(raw, "way", required=False)
    municipality = _text(raw, "municipality", required=True)
    province = _text(raw, "province", required=True)
    country = _text(raw, "country", required=True)
    assert municipality and province and country
    if not _is_spain(country):
        return LossLocation(way=way, municipality=municipality, province=province, country=country)
    canonical = match_province(province)
    if canonical is None:
        raise FieldError(f"'{province}' is not a Spanish province")
    return LossLocation(way=way, municipality=municipality, province=canonical, country=SPAIN)
