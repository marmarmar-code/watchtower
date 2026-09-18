"""Explain revisions for list/feed adapters that previously retained only hashes.

Dates used to poll or sort a list and category ordering are not editorial changes.
Adapters with their own event fingerprints keep their source-specific contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
import re
import unicodedata

from bs4 import BeautifulSoup

from .models import Item

KINDS = frozenset({'rss', 'regjeringen', 'doffin', 'konkurransetilsynet', 'euronext'})
STATE_KEY = 'item_revisions_v1'
LABELS = {
    'title': 'Tittel', 'text': 'Kildetekst', 'buyer': 'Oppdragsgiver',
    'type': 'Kunngjøringstype', 'status': 'Status', 'cpv': 'CPV-koder',
    'deadline': 'Tilbudsfrist', 'estimated_value': 'Anslått verdi',
}


def clean(value: object) -> str:
    text = unescape(str(value))
    if re.search(r'</?[a-zA-Z][^>]*>', text):
        text = BeautifulSoup(text, 'html.parser').get_text(' ', strip=True)
    return ' '.join(unicodedata.normalize('NFC', text).split())


def snapshot(kind: str, item: Item) -> dict[str, str] | None:
    if kind not in KINDS or item.fingerprint is not None:
        return None
    fields = {'title': clean(item.title), 'text': clean(item.text)}
    if kind == 'konkurransetilsynet' and item.published:
        # This adapter includes the explicit first (publication-date) table cell
        # in its searchable row text. Ignore that cell only, never dates inside
        # the merger description or procedural status.
        prefix = clean(item.published) + ' '
        if fields['text'].startswith(prefix):
            fields['text'] = fields['text'][len(prefix):]
    if kind == 'doffin':
        for key in ('buyer', 'type', 'status', 'cpv', 'deadline', 'estimated_value'):
            value = clean(item.metadata.get(key) or '')
            if key == 'cpv':
                codes = re.findall(r'\b\d{8}(?:-\d)?\b', value)
                value = ', '.join(sorted(set(codes))) if codes else value
            if key == 'deadline' and value:
                try:
                    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    value = (stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp).isoformat()
                except ValueError:
                    pass
            fields[key] = value
    return fields


def excerpt_change(before: str, after: str) -> tuple[str, str]:
    """Keep the changed words in view, even near the end of a long description."""
    if max(len(before), len(after)) <= 200:
        return before or 'ikke oppgitt', after or 'ikke oppgitt'
    old, new = before.split(), after.split()
    changed = [op for op in SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
               if op[0] != 'equal']
    if not changed:
        return before[:200], after[:200]
    _, i, j, a, b = changed[0]

    def part(words, start, end):
        left, right = max(0, start - 5), min(len(words), max(start + 1, end) + 5)
        value = ' '.join(words[left:right])
        if len(value) > 190:
            value = value[:190] + '…'
        return ('…' if left else '') + value + ('…' if right < len(words) else '') or 'ikke oppgitt'

    return part(old, i, j), part(new, a, b)


def details(before: dict[str, str] | None, after: dict[str, str]) -> tuple[str, ...]:
    if before is None:
        return tuple(f'{LABELS[key]}: {value}' for key, value in after.items()
                     if key not in {'title', 'text'} and value)
    lines = []
    for key, value in after.items():
        old = before.get(key, '')
        if old != value:
            prior, current = excerpt_change(old, value)
            lines.append(f'{LABELS[key]}: {prior} → {current}')
    return tuple(lines)
