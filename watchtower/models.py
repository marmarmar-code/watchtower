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
    fingerprint: str | None = None
    suppress_alert: bool = False

    def searchable_text(self) -> str:
        parts = [self.title, self.text]
        parts.extend(str(v) for v in self.metadata.values() if v is not None)
        return "\n".join(parts)

    def content_hash(self) -> str:
        if self.fingerprint is not None:
            payload = "\0".join([self.source_id, self.key, self.fingerprint])
        else:
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


@dataclass(frozen=True)
class NotificationEntry:
    source_label: str
    status: str
    title: str
    url: str
    published: str | None = None
    matched_terms: tuple[str, ...] = ()
