from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CollectorSpecies(BaseModel):
    seafood_code: str
    name_zh: str
    name_en: str
    scientific_name: str
    inat_taxon_id: int | None
    gbif_taxon_key: int | None
    commons_category: str | None
    fish_vista_filter: str | None


class CollectorConfig(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    species: list[CollectorSpecies]
