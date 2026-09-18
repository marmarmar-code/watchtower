from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import evaluate, notification_entries
from watchtower.models import Item
from watchtower.notifier import format_slack_entries
from watchtower.state import StateStore
from watchtower.sources.doffin import _item as procurement_item
from watchtower.sources.regjeringen import RegjeringenSource


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceConfig(id='feed', kind='rss', label='Feed', filters=FilterRule(match_all=True))
        self.item = Item('feed', '1', 'Annual result', 'https://example.org/1',
                         text='Revenue was 100 million EUR.', published='2026-09-01',
                         metadata={'categories': 'Finance | Results'})

    def test_list_metadata_and_formatting_do_not_create_updates(self):
        old, _, _ = evaluate(self.source, [self.item], None, max_seen=100)
        changed = replace(self.item, text='<p>Revenue was 100&nbsp;million EUR.</p>',
                          published='2026-09-18', metadata={'categories': 'Results | Finance'})
        _, alerts, _ = evaluate(self.source, [changed], old, max_seen=100)
        self.assertEqual([], alerts)

    def test_update_survives_serialization_and_explains_both_values(self):
        old, _, _ = evaluate(self.source, [self.item], None, max_seen=100)
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            store.save('feed', old)
            changed = replace(self.item, text='Revenue was 120 million EUR.')
            current, alerts, _ = evaluate(self.source, [changed], store.load('feed'), max_seen=100)
            message = format_slack_entries(notification_entries(alerts))
            self.assertIn('Revenue was 100 million EUR. → Revenue was 120 million EUR.', message)
            self.assertIn('https://example.org/1', message)
            self.assertEqual([], evaluate(self.source, [changed], current, max_seen=100)[1])

    def government_source(self):
        return RegjeringenSource(replace(self.source, kind='regjeringen'))

    def government_items(self, source, description):
        xml = f"""<rss><channel><item><guid>report</guid>
          <title>Annual report</title><link>https://example.org/news/report</link>
          <description><![CDATA[{description}]]></description>
        </item></channel></rss>"""
        source.get = Mock(return_value=Mock(content=xml.encode()))
        return source.fetch()

    def test_government_replaced_document_is_explained_and_retained(self):
        source = self.government_source()
        body = '<p>Download <a href="/reports/original.pdf">annual report</a>.</p>'
        first = self.government_items(source, body)
        state, _, _ = evaluate(source.config, first, None, max_seen=100)
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(tmp)
            store.save(source.config.id, state)
            changed = self.government_items(source, body.replace('original.pdf', 'corrected.pdf'))
            current, alerts, _ = evaluate(source.config, changed, store.load(source.config.id), max_seen=100)
            self.assertEqual(1, len(alerts))
            self.assertEqual(('Lenker i kildeteksten: https://example.org/reports/original.pdf → '
                              'https://example.org/reports/corrected.pdf',), alerts[0].item.alert_details)
            message = format_slack_entries(notification_entries(alerts))
            self.assertIn('https://example.org/reports/corrected.pdf', message)
            self.assertEqual([], evaluate(source.config, changed, current, max_seen=100)[1])
            store.save(source.config.id, current)
            removed = self.government_items(source, '<p>Download annual report.</p>')
            _, alerts, _ = evaluate(source.config, removed, store.load(source.config.id), max_seen=100)
            self.assertIn('Lenker i kildeteksten: https://example.org/reports/corrected.pdf → ikke oppgitt', alerts[0].item.alert_details)

    def test_government_link_markup_is_ignored_but_content_parameters_are_kept(self):
        source = self.government_source()
        body = '<p>Download <a href="/reports/document.pdf?version=1&amp;lang=en#page=2">report</a>.</p>'
        first = self.government_items(source, body)
        state, _, _ = evaluate(source.config, first, None, max_seen=100)
        formatted = '<div>Download <a class="document" href="https://example.org/reports/document.pdf?version=1&amp;lang=en#page=2"><strong>report</strong></a>.</div>'
        same = self.government_items(source, formatted)
        self.assertEqual([], evaluate(source.config, same, state, max_seen=100)[1])
        revised = self.government_items(source, formatted.replace('version=1', 'version=2'))
        _, alerts, _ = evaluate(source.config, revised, state, max_seen=100)
        self.assertEqual(1, len(alerts))
        self.assertIn('version=1&lang=en#page=2 →', alerts[0].item.alert_details[0])
        self.assertIn('version=2&lang=en#page=2', alerts[0].item.alert_details[0])

    def test_government_link_snapshot_upgrade_preserves_legacy_history_quietly(self):
        source = self.government_source()
        items = self.government_items(source, '<a href="/reports/document.pdf">Annual report</a>')
        previous = {'initialized': True, 'seen': {'report': 'oldhash', 'outside': 'keep'},
                    'order': ['outside', 'report']}
        state, alerts, _ = evaluate(source.config, items, previous, max_seen=100)
        self.assertEqual([], alerts)
        self.assertEqual('keep', state['seen']['outside'])
        self.assertEqual('https://example.org/reports/document.pdf', state['item_revisions_v1']['report']['links'])

    def test_legacy_hash_is_enriched_without_inventing_old_text(self):
        legacy = {'initialized': True, 'seen': {'1': 'oldhash', 'outside': 'keep'},
                  'order': ['outside', '1']}
        current, alerts, baseline = evaluate(self.source, [self.item], legacy, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertEqual('keep', current['seen']['outside'])
        changed = replace(self.item, title='Corrected annual result')
        self.assertEqual(1, len(evaluate(self.source, [changed], current, max_seen=100)[1]))

    def test_new_post_during_migration_still_alerts(self):
        legacy = {'initialized': True, 'seen': {'outside': 'keep'}, 'order': ['outside']}
        _, alerts, _ = evaluate(self.source, [self.item], legacy, max_seen=100)
        self.assertEqual('new', alerts[0].change)

    def test_procurement_status_and_deadline_explained_but_cpv_order_ignored(self):
        source = replace(self.source, kind='doffin')
        first = replace(self.item, metadata={'status': 'ACTIVE', 'deadline': '2026-09-20T12:00:00Z',
                                            'cpv': '79000000 | 72000000'})
        old, _, _ = evaluate(source, [first], None, max_seen=100)
        same = replace(first, metadata={**first.metadata, 'cpv': '72000000 | 79000000',
                                       'deadline': '2026-09-20T12:00:00+00:00'})
        self.assertEqual([], evaluate(source, [same], old, max_seen=100)[1])
        changed = replace(same, metadata={**same.metadata, 'status': 'CANCELLED'})
        _, alerts, _ = evaluate(source, [changed], old, max_seen=100)
        self.assertEqual(('Status: ACTIVE → CANCELLED',), alerts[0].item.alert_details)

    def test_long_text_diff_displays_the_change_instead_of_only_prefix(self):
        first = replace(self.item, text='Unchanged introduction. ' * 100 + 'Amount: 100 EUR.')
        old, _, _ = evaluate(self.source, [first], None, max_seen=100)
        changed = replace(first, text=first.text.replace('100 EUR', '200 EUR'))
        _, alerts, _ = evaluate(self.source, [changed], old, max_seen=100)
        detail = alerts[0].item.alert_details[0]
        self.assertIn('100 EUR', detail)
        self.assertIn('200 EUR', detail)
        self.assertLess(len(detail), 500)

    def test_source_defined_event_fingerprint_and_details_are_preserved(self):
        first = replace(self.item, fingerprint='before', alert_details=('Known source detail',))
        old, _, _ = evaluate(self.source, [first], None, max_seen=100)
        changed = replace(first, fingerprint='after', alert_details=('Status: pending → granted',))
        _, alerts, _ = evaluate(self.source, [changed], old, max_seen=100)
        self.assertEqual(changed.alert_details, alerts[0].item.alert_details)

    def test_snapshot_retention_follows_seen_retention(self):
        old, _, _ = evaluate(self.source, [self.item], None, max_seen=1)
        new = replace(self.item, key='2')
        state, _, _ = evaluate(self.source, [new], old, max_seen=1)
        self.assertEqual({'2'}, set(state['item_revisions_v1']))

    def test_procurement_money_preserves_supplied_currency_without_guessing(self):
        row = {'id': 'notice-1', 'title': 'Services', 'estimatedValue': {'value': 100, 'currency': 'EUR'}}
        self.assertEqual('100 EUR', procurement_item('feed', row).metadata['estimated_value'])
        row['estimatedValue'] = {'value': 100}
        self.assertEqual('100', procurement_item('feed', row).metadata['estimated_value'])

    def test_merger_publication_cell_is_ignored_but_case_deadline_is_not(self):
        source = replace(self.source, kind='konkurransetilsynet')
        first = replace(self.item, published='17.09.2026', text='17.09.2026 Example merger Frist 20.09.2026')
        state, _, _ = evaluate(source, [first], None, max_seen=100)
        dated = replace(first, published='18.09.2026', text='18.09.2026 Example merger Frist 20.09.2026')
        self.assertEqual([], evaluate(source, [dated], state, max_seen=100)[1])
        changed = replace(dated, text='18.09.2026 Example merger Frist 21.09.2026')
        _, alerts, _ = evaluate(source, [changed], state, max_seen=100)
        self.assertEqual(1, len(alerts))
        self.assertIn('20.09.2026 →', alerts[0].item.alert_details[0])


if __name__ == '__main__':
    unittest.main()
