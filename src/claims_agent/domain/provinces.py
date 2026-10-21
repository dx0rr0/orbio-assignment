"""The 52 Spanish provinces and the spellings declarants use for them."""

import unicodedata

PROVINCES: tuple[str, ...] = (
    "A Coruña",
    "Álava",
    "Albacete",
    "Alicante",
    "Almería",
    "Asturias",
    "Ávila",
    "Badajoz",
    "Baleares",
    "Barcelona",
    "Bizkaia",
    "Burgos",
    "Cáceres",
    "Cádiz",
    "Cantabria",
    "Castellón",
    "Ceuta",
    "Ciudad Real",
    "Córdoba",
    "Cuenca",
    "Gipuzkoa",
    "Girona",
    "Granada",
    "Guadalajara",
    "Huelva",
    "Huesca",
    "Jaén",
    "La Rioja",
    "Las Palmas",
    "León",
    "Lleida",
    "Lugo",
    "Madrid",
    "Málaga",
    "Melilla",
    "Murcia",
    "Navarra",
    "Ourense",
    "Palencia",
    "Pontevedra",
    "Salamanca",
    "Santa Cruz de Tenerife",
    "Segovia",
    "Sevilla",
    "Soria",
    "Tarragona",
    "Teruel",
    "Toledo",
    "Valencia",
    "Valladolid",
    "Zamora",
    "Zaragoza",
)

_ALIASES: dict[str, str] = {
    "La Coruña": "A Coruña",
    "Coruña": "A Coruña",
    "Araba": "Álava",
    "Araba/Álava": "Álava",
    "Alacant": "Alicante",
    "Illes Balears": "Baleares",
    "Islas Baleares": "Baleares",
    "Vizcaya": "Bizkaia",
    "Castelló": "Castellón",
    "Castellón de la Plana": "Castellón",
    "Guipúzcoa": "Gipuzkoa",
    "Gerona": "Girona",
    "Lérida": "Lleida",
    "Orense": "Ourense",
    "Tenerife": "Santa Cruz de Tenerife",
    "València": "Valencia",
    "Nafarroa": "Navarra",
    "Rioja": "La Rioja",
    "Gran Canaria": "Las Palmas",
}


def _key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip().casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_LOOKUP: dict[str, str] = {_key(p): p for p in PROVINCES} | {
    _key(alias): province for alias, province in _ALIASES.items()
}


def match_province(raw: str) -> str | None:
    """Canonical province name for `raw`, ignoring case and accents; None if it isn't one."""
    return _LOOKUP.get(_key(raw))
