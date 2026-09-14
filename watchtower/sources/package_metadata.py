"""Bounded npm tag targets and metadata for the versions they select."""
from __future__ import annotations

from dataclasses import replace
import json
import re
from urllib.parse import quote

from .changes import SnapshotSource, strings
from .common import SourceError

API = 'https://registry.npmjs.org'
PACKAGE = re.compile(r'(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*')
TAG = re.compile(r'[A-Za-z][A-Za-z0-9._-]{0,99}')
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def text(value, label, limit=2000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise SourceError(f'Package {label} is invalid')
    return ' '.join(value.split())


def version(value):
    if not isinstance(value, str) or len(value) > 200 or not VERSION.fullmatch(value):
        raise SourceError('Package version is invalid')
    return value


class PackageMetadataSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError('package_metadata accepts only the public npm registry')
        if self.complete or 'removed' in self.events:
            raise ValueError('Package tag observations cannot confirm package removals')
        self.packages = strings(config.options.get('packages'), 'packages')
        self.tags = strings(config.options.get('tags', ['latest']), 'tags')
        if len(self.packages) > 20 or any(len(p) > 214 or not PACKAGE.fullmatch(p) for p in self.packages):
            raise ValueError('packages must contain at most 20 valid lowercase npm names')
        if len(self.tags) > 5 or any(not TAG.fullmatch(t) for t in self.tags):
            raise ValueError('tags must contain at most five named npm tags')
        if len(self.packages) * len(self.tags) > self.max_records:
            raise ValueError('Package selection exceeds max_records')
        self.field_labels = {'tag_present': 'Tagg oppført', 'version': 'Oppført versjon',
                             'license': 'Oppgitt lisens', 'deprecated': 'Frarådingsmelding',
                             'integrity': 'Publisert integritet', 'shasum': 'Publisert SHA-1', **self.field_labels}

    def _json(self, url):
        response = self.get(url, headers={'Accept': 'application/json'}, stream=True,
                            allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Package registry returned an unexpected redirect')
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError('Package response exceeds max_bytes')
                chunks.append(chunk)
            data = json.loads(b''.join(chunks), object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            if not isinstance(data, dict):
                raise ValueError('Expected JSON object')
            return data
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Package response is invalid JSON') from exc
        finally:
            response.close()

    def _tags(self, package):
        data = self._json(f'{API}/-/package/{quote(package, safe="@")}/dist-tags')
        if not data or 'latest' not in data or len(data) > 100:
            raise SourceError('Package tag listing is empty, incomplete or oversized')
        for key, value in data.items():
            # Unselected tags may use other legal spellings, but remain bounded.
            text(key, 'tag', 100)
            version(value)
        return {tag: data.get(tag) for tag in self.tags}

    def read_records(self):
        rows = []
        for package in sorted(self.packages):
            before = self._tags(package)
            versions = {}
            for target in sorted({v for v in before.values() if v is not None}):
                data = self._json(f'{API}/{quote(package, safe="@")}/{quote(target, safe="")}')
                versions[target] = metadata(data, package, target)
            if self._tags(package) != before:
                raise SourceError('Selected package tag changed during the read; previous state preserved')
            for tag in sorted(self.tags):
                target = before[tag]
                fields = {'tag_present': target is not None, 'version': target,
                          'license': None, 'deprecated': None, 'integrity': None, 'shasum': None}
                if target is not None:
                    fields.update(versions[target])
                rows.append({'key': package + ':' + tag, 'title': package + ' · ' + tag,
                             'url': 'https://www.npmjs.com/package/' + quote(package, safe='@/'),
                             'fields': fields})
        return rows

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = 'Nyobservert pakketagg' if event == 'added' else 'Endret pakketagg eller versjonsmetadata'
        return replace(item, alert_details=(label, *details[1:],
            'Gjelder valgt tagg og dens oppførte versjon; eldre eller mellomliggende versjoner overvåkes ikke',
            'Fravær gjelder taggoppføringen, ikke sletting av pakken. Kontrollsummer er publiserte metadata; pakkefilen er ikke kontrollert'))


def metadata(data, package, target):
    if data.get('name') != package or data.get('version') != target:
        raise SourceError('Package response identity differs from the selected version')
    license_value = data.get('license')
    if isinstance(license_value, dict):
        license_value = license_value.get('type')
        if license_value is None:
            raise SourceError('Package legacy license lacks its type')
    if license_value is not None:
        license_value = text(license_value, 'license', 1000)
    deprecated = data.get('deprecated')
    if deprecated is not None:
        deprecated = text(deprecated, 'deprecation message', 4000, empty=True) or None
    dist = data.get('dist')
    if not isinstance(dist, dict):
        raise SourceError('Package distribution metadata is absent')
    shasum = dist.get('shasum')
    if not isinstance(shasum, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', shasum):
        raise SourceError('Package published SHA-1 is invalid')
    integrity = dist.get('integrity')
    if integrity is not None:
        integrity = text(integrity, 'integrity', 2000)
        if any(not re.fullmatch(r'sha(?:1|256|384|512)-[A-Za-z0-9+/]+={0,2}', token) for token in integrity.split()):
            raise SourceError('Package published integrity is invalid')
        integrity = ' '.join(sorted(set(integrity.split())))
    return {'license': license_value, 'deprecated': deprecated, 'integrity': integrity, 'shasum': shasum.lower()}
