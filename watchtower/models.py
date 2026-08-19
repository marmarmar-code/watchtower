from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class Item:
    source_id: str
    key: str
    title: str
    url: str
    published: str | None = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [self.title, self.text]
        parts.extend(str(v) for v in self.metadata.values() if v is not None)
        return "\n".join(parts)

    def content_hash(self) -> str:
        payload = "\0".join([
            self.source_id,
            self.key,
            self.title,
            self.url,
            self.published or "",
            self.text,
            repr(sorted(self.metadata.items())),
        ])
        return sha256(payload.encode("utf-8")).hexdigest()
