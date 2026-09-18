from copy import deepcopy
from datetime import date
import unittest
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import notification_entries
from watchtower.sources.common import SourceError
from watchtower.sources.company_financial_notices import CapitalIncreaseNoticesSource, ReconstructionNoticesSource
from test_bankruptcy_notices import page
from test_change_sources import poll, response

ORG = '999999991'
KID = '20260000000001'


def index(capital=True, rows=None, count=None, start=None):
    rows = [(KID, ORG, 'Example AS')] if rows is None else rows
    period = start or ('15.09.2026' if capital else '17.08.2026 til 15.09.2026')
    kind = 'Kapitalforhøyelse' if capital else 'Rekonstruksjonsforhandling'
    body = f'<table><tr><td>Dato</td><td></td><td>{period}</td></tr><tr><td>Sted</td><td>Hele landet</td></tr><tr><td>Kunngjøringstype</td><td>{kind}</td></tr>'
    if rows or count is not None:
        body += f'<tr><td>Antall treff</td><td>{len(rows) if count is None else count}</td></tr>'
    for kid, org, name in rows:
        label = name if capital else ('Forhandling om rekonstruksjon' if kid else '')
        link = f'<a href="hent_en.jsp?kid={kid}&sokeverdi={org}&spraak=nb">{label}</a>'
        cells = ['', link, '', org, '', '', ''] if capital else ['', name, '', org, '', '15.09.2026' if kid else '', '', link, '']
        if capital and ' til ' in period:
            cells[5] = '15.09.2026'
            cells.append('')
        body += '<tr>' + ''.join('<td>' + value + '</td>' for value in cells) + '</tr>'
    return page(body + '</table>' + ('<p>Ingen kunngjøringer funnet.</p>' if not rows else ''))


def detail(capital=True, amount='NOK 1.234.567,89', org=ORG, published='15.09.2026', claim='20.10.2026', meeting=None):
    if capital:
        body = '<h3>Endring av kapital</h3>'
        fields = [('Foretaksnavn:', 'Example AS'), ('Organisasjonsnummer:', org),
                  ('Organisasjonsform:', 'Aksjeselskap'), ('Kapital :', amount)]
    else:
        body = '<h3>Rekonstruksjon - åpning</h3>Ved OSLO TINGRETT er det ved kjennelse åpnet offentlig forhandling om rekonstruksjon for:'
        fields = [('Navn/foretaksnavn :', 'Example AS'), ('Organisasjonsnummer:', org),
                  ('Bransje/stilling:', 'Generic services'), ('Kjennelse avsagt:', '14.09.2026'), ('Saksnr:', '26-1KON-TOSL')]
    body += '<table>' + ''.join(f'<tr><td>{key}</td><td>{value}</td></tr>' for key, value in fields) + '</table>'
    if capital:
        body += 'Foretaksregisteret ' + published
    else:
        meeting = meeting or 'Retten har besluttet at berammelse av fordringshavermøte utsettes.'
        body += (f'Spesifisert oppgave over fordringer meldes til rekonstruktør, Adv. Example Adviser , Private address , '
                 f'E-post example@example.test innen {claim} . Fristdagen er 13.09.2026 . {meeting} '
                 'Spørsmål rettes til rekonstruktør. Brønnøysundregistrene ' + published)
    return page('<table><tr><td>' + page(body).decode('latin1') + '</td></tr></table>')


def source(capital=True, **options):
    kind, cls = ('capital_increase_notices', CapitalIncreaseNoticesSource) if capital else ('reconstruction_notices', ReconstructionNoticesSource)
    return cls(SourceConfig(id='notices', kind=kind, label='Company financial notices', urls=(),
                            filters=FilterRule(match_all=True), options={'allow_empty': True, 'window_days': 1 if capital else 30, **options}))


def install(src, idx=None, details=None, second=None):
    idx = index(src.capital) if idx is None else idx
    details = [detail(src.capital)] if details is None else details
    src.get = Mock(side_effect=[response(body) for body in [idx, *details, idx, *(details if second is None else second)]])


class CompanyFinancialNoticeTests(unittest.TestCase):
    def setUp(self):
        clock = patch('watchtower.sources.bankruptcy_notices.today', return_value=date(2026, 9, 15))
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def test_capital_baseline_repeat_and_exact_nominal_amount(self):
        src = source(); install(src)
        first, alerts = poll(src)
        self.assertEqual([], alerts)
        row = first['source_state']['records']['rows'][KID]['row']
        self.assertEqual('1234567.89', row['fields']['registered_capital'])
        self.assertEqual('NOK', row['fields']['currency'])
        self.assertEqual('2026-09-15', row['published'])
        self.assertNotIn('funding', row['fields'])
        install(src); second, alerts = poll(src, first)
        self.assertEqual(first, second); self.assertEqual([], alerts)

    def test_capital_correction_has_before_after_once(self):
        src = source(); install(src); first, _ = poll(src)
        install(src, details=[detail(amount='NOK 1.234.600,00')])
        second, alerts = poll(src, first)
        self.assertEqual(1, len(alerts))
        self.assertIn('Registrert aksjekapital: 1234567.89 → 1234600.00', ' '.join(notification_entries(alerts)[0].details))
        self.assertIn('ikke innhentet finansiering', ' '.join(alerts[0].item.alert_details))
        install(src, details=[detail(amount='NOK 1.234.600,00')]); self.assertEqual([], poll(src, second)[1])

    def test_correction_document_preserves_historical_context(self):
        corrected = detail().replace(b'<h3>Endring av kapital</h3>',
            '<h3>Rettelse</h3>Gjelder registreringsvedtak av 01.01.2010 der kapital ble utelatt. Rettelse av kapital '.encode('latin1'))
        src = source(); install(src, details=[corrected]); state, _ = poll(src)
        fields = state['source_state']['records']['rows'][KID]['row']['fields']
        self.assertEqual('Rettelse', fields['notice_type'])
        self.assertIn('01.01.2010', fields['notice_note'])
        self.assertEqual('2026-09-15', fields['announced_date'])
        # A new notice ID still denotes a correction, not fresh financing.
        install(src, index(rows=[('20260000000002', ORG, 'Example AS')]), [corrected])
        _, alerts = poll(src, state)
        self.assertEqual(1, len(alerts)); self.assertEqual('Rettelse av registrert aksjekapital', alerts[0].item.alert_details[0])
        delivered = ' '.join(notification_entries(alerts)[0].details)
        for expected in ('01.01.2010', '1234567.89', 'NOK', '2026-09-15', 'ikke innhentet finansiering'):
            self.assertIn(expected, delivered)

    def test_reconstruction_dates_meeting_and_no_contact_data(self):
        src = source(False); install(src); first, alerts = poll(src)
        self.assertEqual([], alerts)
        fields = first['source_state']['records']['rows'][KID]['row']['fields']
        self.assertEqual(['2026-09-15', '2026-09-14', '2026-10-20', '2026-09-13'],
                         [fields[key] for key in ('announced_date', 'decision_date', 'claim_deadline', 'fristdag')])
        self.assertEqual('Adv. Example Adviser', fields['reconstructor'])
        self.assertIn('utsettes', fields['meeting_notice'])
        self.assertNotIn('example@example.test', str(first)); self.assertNotIn('Private address', str(first))
        install(src); self.assertEqual((first, []), poll(src, first))
        install(src, details=[detail(False, claim='21.10.2026', meeting='Det avholdes fordringshavermøte 22.10.2026 kl. 13:00.')])
        _, alerts = poll(src, first)
        self.assertEqual(1, len(alerts)); self.assertIn('2026-10-20 → 2026-10-21', ' '.join(alerts[0].item.alert_details))
        delivered = notification_entries(alerts)[0].details
        self.assertLessEqual(len(delivered), 8)
        self.assertTrue(all(len(value) <= 500 for value in delivered))
        for expected in ('2026-10-20 → 2026-10-21', '2026-09-13', '2026-09-14', '2026-09-15',
                         '22.10.2026 kl. 13:00', 'Adv. Example Adviser', 'ikke at rekonstruksjonen er avsluttet'):
            self.assertIn(expected, ' '.join(delivered))

    def test_new_reconstruction_notification_retains_deadlines_and_meeting(self):
        src = source(False); install(src); state, _ = poll(src)
        meeting = 'Det avholdes fordringshavermøte 22.10.2026 kl. 13:00.'
        install(src, index(False, rows=[('20260000000002', ORG, 'Example AS')]), [detail(False, meeting=meeting)])
        _, alerts = poll(src, state)
        delivered = ' '.join(notification_entries(alerts)[0].details)
        for expected in ('2026-10-20', '2026-09-13', '2026-09-14', '2026-09-15', meeting,
                         'Adv. Example Adviser', 'ikke at rekonstruksjonen er avsluttet'):
            self.assertIn(expected, delivered)

    def test_identity_is_notice_not_company_and_order_is_irrelevant(self):
        src = source(); rows = [(KID, ORG, 'Example AS'), ('20260000000002', ORG, 'Example AS')]
        install(src, index(rows=rows), [detail(), detail()]); first, _ = poll(src)
        self.assertEqual(2, len(first['source_state']['records']['rows']))
        install(src, index(rows=list(reversed(rows))), [detail(), detail()]); self.assertEqual((first, []), poll(src, first))

    def test_capital_range_has_explicit_list_dates_and_empty_requires_marker(self):
        src = source(window_days=3)
        install(src, index(start='13.09.2026 til 15.09.2026'))
        self.assertEqual(1, len(src.read_records()))
        bad = index(start='13.09.2026 til 15.09.2026').replace(b'<td>15.09.2026</td>', b'<td>12.09.2026</td>')
        install(src, bad)
        with self.assertRaisesRegex(SourceError, 'outside'): src.read_records()
        for capital in (True, False):
            src = source(capital)
            install(src, index(capital, rows=[]).replace(b'Ingen kunngj', b'Unknown kunngj'), [])
            with self.assertRaisesRegex(SourceError, 'no-results'): src.read_records()

    def test_filtered_and_empty_reads_are_quiet_without_closure_events(self):
        for capital in (True, False):
            src = source(capital); install(src); first, _ = poll(src)
            install(src, index(capital, rows=[]), []); empty, alerts = poll(src, first)
            self.assertEqual([], alerts); self.assertEqual(first['seen'], empty['seen'])
            install(src); self.assertEqual([], poll(src, empty)[1])
            src = source(capital, orgnrs=['999999992']); install(src, details=[])
            self.assertEqual([], src.read_records()); self.assertEqual(2, src.get.call_count)

    def test_amounts_are_decimal_and_malformed_values_fail_closed(self):
        for amount in ('NOK 1,234.56', 'NOK -1,00', 'NOK 1.23,00', 'NOK 1,00 extra', 'NOK NaN', '1,00'):
            src = source(); install(src, details=[detail(amount=amount)])
            with self.subTest(amount=amount), self.assertRaises(SourceError): src.read_records()
        src = source(); install(src, details=[detail(amount='EUR 0,00')])
        self.assertEqual('0.00', src.read_records()[0]['fields']['registered_capital'])

    def test_list_filter_count_duplicates_link_and_window_are_checked(self):
        for capital in (True, False):
            bads = [index(capital, count=2), index(capital, rows=[(KID, ORG, 'Example AS')] * 2),
                    index(capital, start='01.09.2026'), index(capital).replace(b'Hele landet', b'Other place'),
                    index(capital).replace(b'hent_en.jsp?', b'https://example.test/hent_en.jsp?')]
            for bad in bads:
                src = source(capital); install(src, idx=bad)
                with self.subTest(capital=capital), self.assertRaises(SourceError): src.read_records()

    def test_detail_identity_required_fields_and_dates_are_checked(self):
        for capital in (True, False):
            bads = [detail(capital, org='999999992'), detail(capital, published='16.09.2026'),
                    detail(capital).replace(b'Example AS', b'Other AS'), detail(capital).replace(b'Organisasjonsnummer:', b'Missing:')]
            if not capital: bads.append(detail(False, claim='31.02.2026'))
            for bad in bads:
                src = source(capital); install(src, details=[bad])
                with self.subTest(capital=capital), self.assertRaises(SourceError): src.read_records()

    def test_second_complete_read_drift_preserves_input_state(self):
        for capital in (True, False):
            src = source(capital); install(src); first, _ = poll(src); saved = deepcopy(first)
            changed = detail(capital, amount='NOK 2,00', claim='21.10.2026')
            install(src, second=[changed])
            with self.assertRaisesRegex(SourceError, 'between complete reads'): poll(src, first)
            self.assertEqual(saved, first)

    def test_bounds_unknown_phase_truncation_and_window_regression(self):
        for capital in (True, False):
            src = source(capital, max_details=1)
            install(src, index(capital, rows=[(KID, ORG, 'Example AS'), ('20260000000002', ORG, 'Example AS')]), [])
            with self.assertRaises(SourceError): src.read_records()
            src = source(capital); install(src, index(capital)[:-20])
            with self.assertRaisesRegex(SourceError, 'incomplete'): src.read_records()
        src = source(False); install(src, index(False).replace(b'Forhandling om rekonstruksjon', b'Unknown phase'))
        with self.assertRaisesRegex(SourceError, 'type changed'): src.read_records()
        src = source(); install(src); first, _ = poll(src)
        first['source_state']['records']['window_end'] = '2026-09-16'
        install(src)
        with self.assertRaisesRegex(SourceError, 'regressed'): poll(src, first)

    def test_configuration_rejects_removal_unbounded_dates_and_bad_orgs(self):
        for capital in (True, False):
            for options in ({'events': ['removed']}, {'complete_snapshot': True}, {'window_days': 91},
                            {'orgnrs': ['bad']}, {'max_results': 5000}):
                with self.subTest(options=options), self.assertRaises(ValueError): source(capital, **options)


if __name__ == '__main__':
    unittest.main()
