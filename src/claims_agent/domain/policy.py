from datetime import date

from pydantic import BaseModel, ConfigDict


class Policy(BaseModel):
    """Car insurance contract covering one vehicle during its coverage period (dates inclusive)."""

    model_config = ConfigDict(frozen=True)

    number: str
    holder_name: str
    plate: str
    start_date: date
    end_date: date
