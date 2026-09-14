from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.customs_quotas import CustomsQuotasSource, DATA_URL
from test_change_sources import poll, response


def quota(**changes):
    value = {"dokumentnummer": "T010126B", "landgruppekode": "TEF", "maksMengde": "1000", "avskrevetMengde": "800",
             "mengdeenhet": "K", "mengdeenhetBeskrivelse": "Kg", "varenumre": ["02013001", "02013009"],
             "fomdato": "2026-01-01", "tomdato": "2026-12-31"}
    return {**value, **changes}


def source(**options):
    return CustomsQuotasSource(SourceConfig(id="quota", kind="customs_quotas", label="Customs quota", urls=(DATA_URL,),
                              filters=FilterRule(match_all=True), options=options))


def reply(source, *rows):
    source.get = Mock(return_value=response({"versjon": "1.0", "tollkvoter": list(rows)}))


class CustomsQuotasTests(unittest.TestCase):
    def test_quiet_baseline_and_material_band_transitions(self):
        s = source(); reply(s, quota())
        state, alerts = poll(s); self.assertEqual([], alerts)
        reply(s, quota(avskrevetMengde="850"))
        state, alerts = poll(s, state); self.assertEqual([], alerts)
        reply(s, quota(avskrevetMengde="900"))
        state, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        self.assertIn("Lav restkvote", " ".join(alerts[0].item.alert_details))
        reply(s, quota(avskrevetMengde="990"))
        state, alerts = poll(s, state); self.assertEqual([], alerts)
        reply(s, quota(avskrevetMengde="1000"))
        state, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        self.assertIn("Oppbrukt", " ".join(alerts[0].item.alert_details))
        reply(s, quota(avskrevetMengde="990"))
        _, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        self.assertIn("Beregnet rest: 10", " ".join(alerts[0].item.alert_details))

    def test_capacity_validity_new_identity_and_value_unit_without_invented_currency(self):
        s = source(); reply(s, quota())
        state, _ = poll(s)
        reply(s, quota(maksMengde="2000", tomdato="2027-01-31"))
        state, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        self.assertIn("Maksimal kvote: 1000 → 2000", " ".join(alerts[0].item.alert_details))
        value = quota(dokumentnummer="T020126A", mengdeenhet="V", mengdeenhetBeskrivelse="Verdi")
        reply(s, quota(maksMengde="2000", tomdato="2027-01-31"), value)
        _, alerts = poll(s, state); self.assertEqual(1, len(alerts))
        detail = " ".join(alerts[0].item.alert_details)
        self.assertIn("uten valuta", detail); self.assertNotIn("NOK", detail)
        self.assertIsNone(alerts[0].item.published)

    def test_order_decimal_format_and_extra_metadata_do_not_alert(self):
        s = source(); reply(s, quota())
        state, _ = poll(s)
        reply(s, quota(maksMengde="1000.000", avskrevetMengde="800.00", varenumre=["02013009", "02013001"], refreshed="later"))
        after, alerts = poll(s, state)
        self.assertEqual([], alerts); self.assertEqual(state, after)
        reply(s, quota(avskrevetMengde="1001"))
        _, alerts = poll(s, state)
        self.assertIn("Beregnet rest: -1", " ".join(alerts[0].item.alert_details))

    def test_invalid_schema_amount_unit_dates_identity_and_partial_payload_fail_closed(self):
        bad = [quota(**{k:v}) for k,v in [('dokumentnummer',''), ('landgruppekode',False), ('maksMengde','0'),
                ('avskrevetMengde','-1'), ('avskrevetMengde',1.5), ('maksMengde','NaN'), ('mengdeenhet','L'),
                ('mengdeenhetBeskrivelse','NOK'), ('varenumre',['02013001','02013001']), ('varenumre',['2013001']),
                ('tomdato','2025-12-31'), ('fomdato','2026-02-30')]]
        for row in bad:
            s = source(); reply(s, row)
            with self.subTest(row=row), self.assertRaises(SourceError): s.read_records()
        for payload in ({'versjon':'2.0','tollkvoter':[quota()]}, {'versjon':'1.0','tollkvoter':[]},
                        {'versjon':'1.0','tollkvoter':[quota(),quota()]},
                        {'versjon':'1.0','tollkvoter':[quota()], 'next':'another-page'}):
            s = source(); s.get = Mock(return_value=response(payload))
            with self.assertRaises(SourceError): s.read_records()
        s=source(max_records=1);reply(s,quota(),quota(dokumentnummer='T020126A'))
        with self.assertRaises(SourceError): s.read_records()

    def test_invalid_removal_or_threshold_options_are_rejected(self):
        for options in ({'events':['removed']}, {'complete_snapshot':True}, {'low_remaining_percent':0}, {'low_remaining_percent':True}):
            with self.assertRaises(ValueError): source(**options)


if __name__ == '__main__':
    unittest.main()
