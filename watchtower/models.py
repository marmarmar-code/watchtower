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
    alert_details: tuple[str, ...] = ()

    def searchable_text(self) -> str:
        parts = [self.title, self.text, *self.alert_details]
        parts.extend(str(v) for v in self.metadata.values() if v is not None)
        return "\n".join(parts)

    def _fallback_hash_payload(self, *, include_alert_details: bool) -> str:
        parts = [
            self.source_id,
            self.key,
            self.title,
            self.url,
            self.published or "",
            self.text,
            repr(sorted(self.metadata.items())),
        ]
        if include_alert_details:
            parts.append(repr(self.alert_details))
        return "\0".join(parts)

    def content_hash(self) -> str:
        if self.fingerprint is not None:
            payload = "\0".join([self.source_id, self.key, self.fingerprint])
        else:
            # Notification presentation must not change an item's persisted identity.
            payload = self._fallback_hash_payload(include_alert_details=False)
        return sha256(payload.encode("utf-8")).hexdigest()

    def compatible_content_hashes(self) -> tuple[str, ...]:
        """Return the canonical digest plus the short-lived 0.1.0 transition digest."""
        canonical = self.content_hash()
        if self.fingerprint is not None:
            return (canonical,)

        transitional_payload = self._fallback_hash_payload(include_alert_details=True)
        transitional = sha256(transitional_payload.encode("utf-8")).hexdigest()
        if transitional == canonical:
            return (canonical,)
        return (canonical, transitional)


@dataclass(frozen=True)
class NotificationEntry:
    source_label: str
    status: str
    title: str
    url: str
    published: str | None = None
    matched_terms: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
