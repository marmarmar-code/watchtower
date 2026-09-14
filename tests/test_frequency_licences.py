import copy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.frequency_licences import DATA_URL, FrequencyLicencesSource
from test_change_sources import poll, response


def row(**changes):
    value = {
        "id": 101,
        "lowerFrequency": 440000000,
        "higherFrequency": 440100000,
        "application": "Land mobile",
        "company": "Example Communications AS",
        "expiry": "2026-12-31T23:00:00+0000",
        "nationalcoverage": True,
        "localcoverage": "Oslo",
        "shortComments": "Test permit",
        "rightofuseinfono": "101",
        "duplex": False,
        "downlinkLowerFrequency": 0,
        "downlinkHigherFrequency": 0,
        "uplinkLowerFrequency": 0,
        "uplinkHigherFrequency": 0,
    }
    value.update(changes)
    return value


def source(**options):
    return FrequencyLicencesSource(SourceConfig(
        id="frequency-licences", kind="frequency_licences", label="Frequency licences",
        urls=(), filters=FilterRule(match_all=True), options={"max_bytes": 1_000_000, **options}))


def install_reads(src, first, second=None, *, status=200):
    second = first if second is None else second
    src.get = Mock(side_effect=[response(first, status=status), response(second, status=status)])


class FrequencyLicenceTests(unittest.TestCase):
    def test_full_sweep_baseline_repeat_and_substantive_changes(self):
        records = [row(), row(id=102, rightofuseinfono="102", duplex=True,
                             lowerFrequency=0, higherFrequency=0, downlinkLowerFrequency=450000000, downlinkHigherFrequency=450100000,
                             uplinkLowerFrequency=460000000, uplinkHigherFrequency=460100000)]
        src = source()
        install_reads(src, records)
        previous, alerts = poll(src)
        self.assertEqual([], alerts)
        install_reads(src, list(reversed(records)))
        repeat, alerts = poll(src, previous)
        self.assertEqual([], alerts)
        self.assertEqual(previous, repeat)
        changed = [row(company="Changed Communications AS", expiry="2027-01-01T00:00:00+0000",
                       lowerFrequency=0, higherFrequency=440100000, localcoverage="Nationwide")]
        install_reads(src, changed)
        changed_state, alerts = poll(src, repeat)
        self.assertEqual(1, len(alerts))
        self.assertNotEqual(previous, changed_state)
        self.assertEqual('changed', alerts[0].item.metadata['event'])
        detail=' '.join(alerts[0].item.alert_details)
        for expected in ('Changed Communications','2027-01-01','440000000','Nationwide'):
            self.assertIn(expected,detail)
        install_reads(src, changed)
        unchanged, alerts = poll(src, changed_state)
        self.assertEqual([],alerts)
        self.assertEqual(changed_state,unchanged)

    def test_general_rule_permit_filter_is_exact_and_missing_is_an_error(self):
        src = source(permit_numbers=["9999999"], include_general_rules=True)
        install_reads(src, [row(rightofuseinfono="9999999"), row(id=102, rightofuseinfono="99999990")])
        self.assertEqual(1, len(src.read_records()))
        src = source(permit_numbers=["999"])
        install_reads(src, [row()])
        with self.assertRaises(SourceError):
            src.read_records()

    def test_schema_types_dates_ranges_and_duplicates_fail_closed(self):
        bads = [
            [row(id=True)], [row(id=-1)], [row(id=101), row(id=101)],
            [row(nationalcoverage=1)], [row(expiry="2026-12-31")],
            [row(lowerFrequency=450, higherFrequency=449)],
            [row(application=None)], [dict(row(), unexpected="x")],
        ]
        for bad in bads:
            with self.subTest(bad=bad):
                src = source(); install_reads(src, bad)
                with self.assertRaises(SourceError): src.read_records()
        src = source(); install_reads(src, [])
        with self.assertRaises(SourceError): src.read_records()

    def test_changed_second_sweep_does_not_advance_state(self):
        src = source(); install_reads(src, [row()]); previous, _ = poll(src)
        saved = copy.deepcopy(previous)
        src.get = Mock(side_effect=[response([row()]), response([row(company="Changed")])])
        with self.assertRaises(SourceError): poll(src, previous)
        self.assertEqual(saved, previous)

    def test_transport_bounds_redirect_and_malformed_json_close_response(self):
        for raw,status in ((b'x'*2048,200),(b'[]',302),(b'not-json',200)):
            with self.subTest(status=status,length=len(raw)):
                src=source(max_bytes=1024);r=response(raw,status=status)
                src.get=Mock(return_value=r)
                with self.assertRaises(SourceError):src.read_records()
                r.close.assert_called_once()
                self.assertEqual(DATA_URL,src.get.call_args.args[0])
                self.assertFalse(src.get.call_args.kwargs['allow_redirects'])

    def test_no_withdrawal_for_absent_record_without_complete_snapshot(self):
        src = source(); install_reads(src, [row()]); previous, _ = poll(src)
        install_reads(src, [row(id=102, rightofuseinfono="102")])
        # A normal source reports the new row; it must not manufacture a removal event.
        _, alerts = poll(src, previous)
        self.assertEqual(['added'],[a.item.metadata['event'] for a in alerts])

    def test_general_defaults_and_shared_display_number_preserve_row_identity(self):
        records=[row(),row(id=102),row(id=103,rightofuseinfono='9999999'),
                 row(id=104,rightofuseinfono='9999998')]
        src=source();install_reads(src,records)
        self.assertEqual(['101','102'],[r['key'] for r in src.read_records()])
        src=source(include_general_rules=True);install_reads(src,records)
        self.assertEqual(4,len(src.read_records()))
        src=source(permit_numbers=['101']);install_reads(src,records)
        self.assertEqual(2,len(src.read_records()))
        for options in ({'permit_numbers':['9999999']},{'include_general_rules':1},
                        {'complete_snapshot':True},{'events':['removed'],'complete_snapshot':True}):
            with self.assertRaises(ValueError):source(**options)

    def test_bad_full_collection_cannot_hide_behind_selected_permit(self):
        src=source(permit_numbers=['101'])
        for records in ([row(),row(id=102,rightofuseinfono='102',expiry='2026-02-30T00:00:00+0000')],
                        [row(),row(id=102,rightofuseinfono='102',lowerFrequency=-1)],
                        [row(),row(id=102,rightofuseinfono='102',duplex=True)]):
            install_reads(src,records)
            with self.assertRaises(SourceError):src.read_records()
        src=source(max_register_records=1);install_reads(src,[row(),row(id=102)])
        with self.assertRaises(SourceError):src.read_records()
        src=source(max_records=1);install_reads(src,[row(),row(id=102)])
        with self.assertRaises(SourceError):src.read_records()
        src=source();src.get=Mock(return_value=response(b'[{"id":101,"id":102}]'))
        with self.assertRaises(SourceError):src.read_records()


if __name__ == "__main__":
    unittest.main()
