import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.parliament_votes import ParliamentVotesSource
from watchtower.sources.common import SourceError
from test_change_sources import poll, response


def source(**options):
    return ParliamentVotesSource(SourceConfig(id='votes', kind='parliament_votes', label='Voteringer',
        urls=(), filters=FilterRule(match_all=True), options={'case_ids': [83657], 'allow_empty': True, **options}))


def xml(**changes):
    fields = {'sak_id': '83657', 'votering_id': '16897', 'votering_tema': 'Forslag',
              'vedtatt': 'false', 'personlig_votering': 'true', 'antall_for': '6',
              'antall_mot': '80', 'antall_ikke_tilstede': '83',
              'votering_resultat_type': 'ikke_spesifisert', 'votering_tid': '2021-06-01T15:09:53.113',
              'respons_dato_tid': '2026-09-11T12:29:05+02:00'}
    fields.update(changes)
    row = '<sak_votering>' + ''.join(f'<{k}>{v}</{k}>' for k,v in fields.items()) + '</sak_votering>'
    return ('<sak_votering_oversikt xmlns="http://data.stortinget.no"><sak_id>83657</sak_id>'
            '<sak_votering_liste>' + row + '</sak_votering_liste></sak_votering_oversikt>').encode()


class ParliamentVotesTests(unittest.TestCase):
    def test_quiet_first_poll_transport_noise_and_changed_outcome(self):
        s = source(); s.get = Mock(return_value=response(xml()))
        state, alerts = poll(s); self.assertEqual([], alerts)
        s.get.return_value = response(xml(respons_dato_tid='2026-09-12T12:00:00+02:00'))
        again, alerts = poll(s, state)
        self.assertEqual(state, again); self.assertEqual([], alerts)
        s.get.return_value = response(xml(vedtatt='true'))
        _, alerts = poll(s, state)
        self.assertEqual(1, len(alerts)); self.assertIsNone(alerts[0].item.published)
        self.assertIn('Endret voteringsresultat', alerts[0].item.alert_details)

    def test_unanimous_vote_does_not_infer_outcome_from_counts(self):
        s = source(); s.get = Mock(return_value=response(xml(vedtatt='true', antall_for='0',
            antall_mot='0', antall_ikke_tilstede='0', personlig_votering='false',
            votering_resultat_type='enstemmig_vedtatt')))
        self.assertTrue(s.read_records()[0]['fields']['adopted'])

    def test_invalid_contracts_fail_closed(self):
        cases = [xml(sak_id='63033'), xml(vedtatt='1'), xml(antall_for='170'),
                 xml(antall_mot='-1'), xml(votering_tid='2026-02-30T12:00:00'),
                 xml(votering_id='0'), xml().replace(b'</sak_votering_liste>',
                    b'<sak_votering><sak_id>83657</sak_id><votering_id>16897</votering_id></sak_votering></sak_votering_liste>'),
                 b'<html/>', xml().replace(b'<sak_id>83657</sak_id>', b'<sak_id>1</sak_id>', 1)]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(SourceError):
                s = source(); s.get = Mock(return_value=response(data)); s.read_records()

    def test_empty_selected_case_is_valid_but_missing_list_is_not(self):
        s = source()
        s.get = Mock(return_value=response(b'<sak_votering_oversikt xmlns="http://data.stortinget.no"><sak_id>83657</sak_id><sak_votering_liste/></sak_votering_oversikt>'))
        self.assertEqual([], s.read_records())
        s.get.return_value = response(b'<sak_votering_oversikt xmlns="http://data.stortinget.no"><sak_id>83657</sak_id></sak_votering_oversikt>')
        with self.assertRaises(SourceError): s.read_records()

    def test_selection_and_removals(self):
        for options in ({'case_ids': [True]}, {'case_ids': [1, 1]}, {'case_ids': []}, {'events': ['removed']}):
            with self.assertRaises(ValueError): source(**options)
