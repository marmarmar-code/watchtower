from __future__ import annotations

from .common import Source, SourceError
from ..models import Item


class DoffinSource(Source):
    """Reserved adapter.

    Doffin's current public frontend is JS-driven and no stable documented query API has
    been verified for this project. Keep disabled until a verified interface (or the
    existing Medier24/NRK implementation) is wired in deliberately.
    """

    def fetch(self) -> list[Item]:
        raise SourceError("Doffin adapter is intentionally disabled pending verified data interface")
