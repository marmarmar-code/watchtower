"""Approved projects in explicit Forskningsrådet call result tables."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .changes import SnapshotSource, document, integer, public_url, strings
from .common import SourceError

HEADERS = ['Prosjektnr.', 'Organisasjon', 'Prosjekttittel', 'Tema', 'Søkt beløp', 'Publisert']
RESULTS_URL = 'https://www.forskningsradet.no/utlysninger/?timeframe=2'
FIFTH_COLUMNS = {'Søkt beløp': 'requested_amount', 'Tildelt beløp': 'awarded_amount',
                 'Gradsgivende institusjon': 'degree_institution'}


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _result_table(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    result_blocks = []
    for script in soup.find_all('script'):
        text = script.get_text()
        if '"ProposalPage"' not in text:
            continue
        match = re.search(r'\bprops:\s*', text)
        if not match:
            raise SourceError('Forskningsrådet page payload is absent')
        try:
            props, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, RecursionError) as exc:
            raise SourceError('Forskningsrådet page payload is invalid JSON') from exc
        result_blocks.extend(obj for obj in _objects(props) if obj.get('guid') == 'results')
    if len(result_blocks) != 1:
        raise SourceError('Forskningsrådet result block is absent or ambiguous')
    tables = [obj.get('table') for obj in _objects(result_blocks[0])
              if obj.get('title') == 'Innvilgede søknader' and 'table' in obj]
    if len(tables) != 1 or not isinstance(tables[0], dict):
        raise SourceError('Forskningsrådet approved table is absent, ambiguous or changed')
    header = tables[0].get('header')
    if (not isinstance(header, list) or len(header) != len(HEADERS)
            or [value.strip() if isinstance(value, str) else value for value in header[:4] + header[5:]]
            != HEADERS[:4] + HEADERS[5:]
            or not isinstance(header[4], str) or header[4].strip() not in FIFTH_COLUMNS):
        raise SourceError('Forskningsrådet approved table is absent, ambiguous or changed')
    rows = tables[0].get('rows')
    if not isinstance(rows, list):
        raise SourceError('Forskningsrådet result rows are invalid')
    return header[4].strip(), rows


def _result_rows(raw):
    """Backward-compatible parser result for the established explicit-page contract."""
    return _result_table(raw)[1]


class ResearchAwardsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        self.suppressed_call_urls = frozenset(strings(
            config.options.get('suppress_call_urls', []), 'suppress_call_urls', empty=True))
        for url in self.suppressed_call_urls:
            parsed = urlparse(public_url(url))
            if (parsed.hostname != 'www.forskningsradet.no' or parsed.query or parsed.fragment
                    or not re.fullmatch(r'/utlysninger/\d{4}/[^/]+/', parsed.path)):
                raise ValueError('suppress_call_urls requires exact official call URLs')
        # Route notifications without changing selected records or existing scope.
        snapshot_config = replace(config, options={
            key: value for key, value in config.options.items() if key != 'suppress_call_urls'})
        super().__init__(snapshot_config, *args, **kwargs)
        self.config = config
        self.discovery = config.options.get('discover_results', False)
        if not isinstance(self.discovery, bool):
            raise ValueError('discover_results must be boolean')
        self.urls = tuple(public_url(url) for url in config.urls)
        if self.discovery:
            if self.urls:
                raise ValueError('Discovery mode uses the fixed official result listing, not explicit urls')
            years = config.options.get('discovery_years')
            if (not isinstance(years, list) or not years or len(years) > 3
                    or any(type(year) is not int or not 2023 <= year <= datetime.now().year for year in years)
                    or len(years) != len(set(years))):
                raise ValueError('discovery_years must contain 1-3 unique years from 2023 through the current year')
            self.discovery_years = set(years)
            self.max_discovery_calls = integer(config.options.get('max_discovery_calls', 10),
                                               'max_discovery_calls', 1, 30)
            integer(config.options.get('max_listing_pages', 1), 'max_listing_pages', 1, 1)
            self.allow_empty = True
        elif not 1 <= len(self.urls) <= 10 or len(set(self.urls)) != len(self.urls):
            raise ValueError('urls must contain 1-10 distinct Forskningsrådet call pages')
        if any(urlparse(url).hostname != 'www.forskningsradet.no' or not urlparse(url).path.startswith('/utlysninger/') for url in self.urls):
            raise ValueError('urls must be official Forskningsrådet /utlysninger/ pages')
        if self.complete or 'removed' in self.events:
            raise ValueError('Call result pages cannot confirm removals')
        self.field_labels = {'project_number': 'Prosjektnummer', 'organisation': 'Organisasjon',
            'project_title': 'Prosjekttittel', 'theme': 'Tema', 'requested_amount': 'Søkt beløp (kroner)',
            'awarded_amount': 'Tildelt beløp (kroner)', 'degree_institution': 'Gradsgivende institusjon',
            'outcome': 'Utfall', **self.field_labels}

    def _item(self, row, event, details, suppress):
        return super()._item(row, event, details,
                             suppress or row['url'] in self.suppressed_call_urls)

    def read_records(self):
        records = []
        urls = self._discovered_urls() if self.discovery else self.urls
        for url in urls:
            fifth_heading, result_rows = _result_table(document(self, url))
            fifth_field = FIFTH_COLUMNS[fifth_heading]
            for cells in result_rows:
                if not isinstance(cells, list) or len(cells) != len(HEADERS) or any(not isinstance(cell, dict) for cell in cells):
                    raise SourceError('Forskningsrådet result row is incomplete')
                values = [cell.get('value') for cell in cells]
                if any(not isinstance(value, str) for value in values) or any(not values[i].strip() for i in (0, 1, 2, 4)):
                    raise SourceError('Forskningsrådet result row has invalid text fields')
                if not re.fullmatch(r'\d+', values[0].strip()):
                    raise SourceError('Forskningsrådet project number is invalid')
                fifth_value = values[4].strip()
                if fifth_field != 'degree_institution':
                    fifth_value = re.sub(r'\s+', '', fifth_value)
                    if not re.fullmatch(r'\d+(?:[.,]\d+)?', fifth_value):
                        raise SourceError('Forskningsrådet amount is invalid')
                published = cells[5].get('displayValue')
                try:
                    if not isinstance(published, str):
                        raise ValueError
                    published = datetime.strptime(published, '%d.%m.%Y').date().isoformat()
                except ValueError:
                    raise SourceError('Forskningsrådet publication date is invalid') from None
                project = values[0].strip()
                records.append({'key': f'{url}#{project}', 'title': values[2].strip(), 'url': url,
                    'published': published, 'fields': {'project_number': project,
                    'organisation': values[1].strip(), 'project_title': values[2].strip(),
                    'theme': values[3].strip(), fifth_field: fifth_value if fifth_field == 'degree_institution'
                    else fifth_value.replace(',', '.'),
                    'outcome': 'Innvilget søknad'}})
                if len(records) > self.max_records:
                    raise SourceError('Forskningsrådet results exceed max_records')
        return records

    def _discovered_urls(self):
        soup = BeautifulSoup(document(self, RESULTS_URL), 'html.parser')
        # The official page declares a count per group and embeds hidden cards.
        # A partial/lazy-loaded page must fail before any project state is accepted.
        groups = soup.select('.proposal-group')
        if not groups or soup.select('[rel="next"], .pagination, [data-next-page]'):
            raise SourceError('Forskningsrådet result listing is absent or paginated')
        counted = 0
        for group in groups:
            header = group.select_one('.proposal-group--header')
            match = re.search(r'\((\d+) utlysning(?:er)?\)', header.get_text(' ', strip=True)) if header else None
            count = len(group.select('.proposal'))
            if not match or int(match.group(1)) != count:
                raise SourceError('Forskningsrådet declared group count does not match embedded calls')
            counted += count
        proposals = soup.select('.proposal')
        if counted != len(proposals):
            raise SourceError('Forskningsrådet calls are outside the counted groups')
        if not proposals:
            raise SourceError('Forskningsrådet result listing contains no proposal cards')
        urls, all_urls = [], set()
        for proposal in proposals:
            status = proposal.select_one('.status')
            link = proposal.select_one('a.proposal--link[href]')
            if status is None:
                raise SourceError('Forskningsrådet proposal card lacks status')
            text = status.get_text(' ', strip=True)
            marked = 'status--result-is-published' in status.get('class', [])
            if marked != (text == 'Se Resultat'):
                raise SourceError('Forskningsrådet result status marker changed')
            if not marked:
                continue
            if link is None:
                raise SourceError('Forskningsrådet result card lacks its call link')
            url = urljoin(RESULTS_URL, link['href']).split('#', 1)[0]
            parsed = urlparse(url)
            match = re.fullmatch(r'/utlysninger/(\d{4})/[^/]+/', parsed.path)
            if (parsed.scheme != 'https' or parsed.hostname != 'www.forskningsradet.no'
                    or parsed.query or not match):
                raise SourceError('Forskningsrådet result link is outside the supported original pages')
            if url in all_urls:
                raise SourceError('Forskningsrådet result listing repeated a call URL')
            all_urls.add(url)
            if int(match.group(1)) in self.discovery_years:
                urls.append(url)
        if len(urls) > self.max_discovery_calls:
            raise SourceError('Forskningsrådet selected result calls exceed max_discovery_calls')
        return tuple(urls)
