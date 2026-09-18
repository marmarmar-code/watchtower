from copy import deepcopy
import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.aviation_operators import AviationOperatorsSource, PAGE, TITLE
from watchtower.sources.common import SourceError
from test_change_sources import poll, response


def html(entries, start=1, total=None, pages=1):
    total = total or len(entries)
    cards = ''.join(f'<div class="m__article-result"><h2>{name}</h2><p class="m__article-result-text">{address}</p>'
                    f'<span class="a-small-label">AOC-nummer: {number}</span></div>' for number,name,address in entries)
    links = ''.join(f'<a href="{PAGE if n==1 else PAGE+"?p="+str(n)}">{n}</a>' for n in range(1,pages+1))
    return (f'<html><h1>{TITLE}</h1><div class="m-article-list"><span class="a-small-label">'
            f'Viser treff {start} til {start+len(entries)-1} av {total}</span>{cards}'
            f'<div class="m__article-pagination">{links}</div></div></html>').encode()


FIRST = ('NO.AOC.001', 'Example Air AS', 'Example street 1')
SECOND = ('SCA.AOC.002', 'Example Flight AS', 'Example street 2')


def source(pages=None):
    src = AviationOperatorsSource(SourceConfig(id='operators',kind='aviation_operators',filters=FilterRule(match_all=True)))
    pages = pages or [html([FIRST, SECOND])]
    src.get = Mock(side_effect=[response(page) for page in pages+pages])
    return src


class AviationOperatorTests(unittest.TestCase):
    def test_complete_pagination_quiet_baseline_and_reorder(self):
        state, alerts = poll(source([html([FIRST],total=2,pages=2),html([SECOND],start=2,total=2,pages=2)]))
        self.assertFalse(alerts)
        self.assertEqual(2,len(state['source_state']['records']['rows']))
        self.assertFalse(poll(source([html([SECOND,FIRST])]),state)[1])

    def test_changed_operator_retains_certificate_and_explains_once(self):
        state,_ = poll(source())
        changed = (FIRST[0], 'Example New Air AS', 'Example street 3')
        updated,alerts = poll(source([html([changed,SECOND])]),state)
        self.assertEqual(1,len(alerts))
        self.assertIn('Example Air AS → Example New Air AS',str(alerts[0].item.alert_details))
        self.assertIn('Example street 1 → Example street 3',str(alerts[0].item.alert_details))
        self.assertIsNone(alerts[0].item.published)
        self.assertFalse(poll(source([html([changed,SECOND])]),updated)[1])

    def test_absence_keeps_history_without_withdrawal_claim(self):
        state,_ = poll(source());updated,alerts = poll(source([html([SECOND])]),state)
        self.assertFalse(alerts)
        self.assertIn(FIRST[0],updated['source_state']['records']['rows'])
        self.assertFalse(poll(source(),updated)[1])

    def test_missing_page_duplicate_and_drifting_reads_preserve_state(self):
        state,_ = poll(source());prior=deepcopy(state)
        samples = [source([html([FIRST],total=2,pages=2),html([FIRST],start=2,total=2,pages=2)]),
                   source([html([FIRST],total=2,pages=2),html([SECOND],start=1,total=2,pages=2)])]
        drifting=source();drifting.get=Mock(side_effect=[response(html([FIRST,SECOND])),response(html([FIRST]))]);samples.append(drifting)
        for src in samples:
            with self.assertRaises(SourceError):src.fetch_with_state(state)
            self.assertEqual(prior,state)

    def test_schema_truncation_and_pagination_bounds(self):
        for page in [html([FIRST])[:-7],html([FIRST]).replace(b'AOC-nummer:',b'Unknown:'),
                     html([FIRST],total=2,pages=1),html([FIRST]).replace(PAGE.encode(),b'https://example.org/')]:
            with self.assertRaises(SourceError):source()._page(page,1)


if __name__ == '__main__':unittest.main()
