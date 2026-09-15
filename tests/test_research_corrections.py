import csv,io,tempfile,unittest
from copy import deepcopy
from datetime import date
from unittest.mock import Mock,patch
from watchtower.config import SourceConfig,FilterRule,Config
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.common import SourceError
from watchtower.sources.research_corrections import ResearchCorrectionsSource,HEADERS,doi
from test_change_sources import response,poll


def row(ident='12345',country='Norway',nature='Retraction',**changes):
    r=dict(zip(HEADERS,[ident,'Example publication','Medicine;Biology;','Example institute','Example journal','Example publisher',country,'Example author;Second author','','Research Article;','7/30/2026 0:00','10.1234/example-notice','0','12/8/2024 0:00','10.1234/example-original','0',nature,'Source label;','No','','']))
    r.update(changes);return [r[k] for k in HEADERS]


def export(rows=None,trailer=2,headers=None):
    out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(HEADERS if headers is None else headers);writer.writerows([row()] if rows is None else rows);writer.writerows([['']*21]*trailer);return out.getvalue().encode()


def source(**options):return ResearchCorrectionsSource(SourceConfig(id='research',kind='research_corrections',label='Research updates',urls=(),filters=FilterRule(match_all=True),options=options))


def readme(stamp='2026-09-14'):return ('# Retraction Watch Data\n\nThis repository contains the latest dataset from Retraction Watch, generated on '+stamp+'.\n').encode()

def install(s,raw=None,second=None,stamp='2026-09-14',second_stamp=None):
    raw=export() if raw is None else raw;s.get=Mock(side_effect=[response(readme(stamp)),response(raw),response(readme(stamp if second_stamp is None else second_stamp)),response(raw if second is None else second)])


class ResearchCorrectionsTests(unittest.TestCase):
    def setUp(self):
        p=patch('watchtower.sources.research_corrections.today',return_value=date(2026,9,15));p.start();self.addCleanup(p.stop)

    def test_initial_repeat_country_tokens_and_literal_dates(self):
        s=source();install(s,export([row(country='Sweden;Norway;Denmark')]));old,alerts=poll(s);self.assertEqual([],alerts);r=next(iter(s._next['rows'].values()))['row'];self.assertIsNone(r['published']);self.assertEqual('2026-07-30',r['fields']['notice_date']);self.assertEqual('2024-12-08',r['fields']['original_date']);self.assertEqual(2,s.blank_trailer_rows)
        install(s,export([row(country='Sweden;Norway;Denmark')]));new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_all_four_types_are_distinct_and_reclassification_alerts_once(self):
        for nature in ['Correction','Expression of concern','Reinstatement']:
            s=source();install(s);old,_=poll(s);b=export([row(nature=nature)]);install(s,b);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual(nature,s._next['rows']['12345']['row']['fields']['nature']);install(s,b);self.assertEqual([],poll(s,new)[1])

    def test_shared_doi_is_not_record_identity(self):
        s=source();install(s,export([row(),row('12346',nature='Expression of concern')]));old,_=poll(s);self.assertEqual(2,len(s._next['rows']))
        install(s,export([row(),row('12346',nature='Expression of concern'),row('12347',nature='Reinstatement')]));_,alerts=poll(s,old);self.assertEqual(1,len(alerts))

    def test_semicolon_and_percent_escaped_doi_remain_one_identifier(self):
        value='10.1234/Example%3c1%3E;2-Q';s=source();install(s,export([row(**{'RetractionDOI':value})]));poll(s);r=s._next['rows']['12345']['row'];self.assertEqual('10.1234/example<1>;2-q',r['fields']['notice_doi']);self.assertEqual('https://doi.org/10.1234/example%3C1%3E%3B2-q',r['url']);self.assertEqual(value,r['source_doi_text']['notice'])
        self.assertIsNone(doi('Unavailable'))

    def test_link_fallbacks_preserve_missing_values(self):
        for fields,kind in [({'RetractionDOI':'unavailable','RetractionPubMedID':'123456'},'notice_pubmed'),({'RetractionDOI':''},'original_doi'),({'RetractionDOI':'','OriginalPaperDOI':'unavailable','OriginalPaperPubMedID':'123456'},'original_pubmed'),({'RetractionDOI':'','OriginalPaperDOI':'Unavailable'},'dataset')]:
            s=source();install(s,export([row(**fields)]));poll(s);r=s._next['rows']['12345']['row'];self.assertEqual(kind,r['link_kind']);self.assertIsNone(r['fields']['notice_doi'])

    def test_author_order_and_institution_text_are_not_reinterpreted(self):
        s=source();b=export([row(**{'Author':'Same author;Same author','Institution':'Example one;;Example two;'})]);install(s,b);poll(s);f=s._next['rows']['12345']['row']['fields'];self.assertEqual(['Same author','Same author'],f['authors']);self.assertEqual('Example one;;Example two;',f['institutions'])

    def test_reasons_and_subject_order_ignore_source_list_reordering(self):
        s=source();install(s,export([row(**{'Reason':'First;Second;'})]));old,_=poll(s);install(s,export([row(**{'Reason':'Second;First;','Subject':'Biology;Medicine;'})]));_,alerts=poll(s,old);self.assertEqual([],alerts)

    def test_wholly_blank_trailer_only_and_full_identity_checks(self):
        for b in [export([row(),row()]),export([row(ident='')]),export([row(),['']*21,row('12346')]),export([row(**{'':'unexpected'})]),export([row(country='')]),export(headers=HEADERS[:-1]),export()[:-1],export([row()[:-1]])]:
            s=source();install(s,b)
            with self.assertRaises(SourceError):s.read_records()
        s=source();install(s,export([row(),row('12346',country='Sweden'),row('12346',country='Sweden')]))
        with self.assertRaises(SourceError):s.read_records()

    def test_country_selection_is_exact_and_absence_is_not_reinstatement(self):
        s=source();install(s,export([row(),row('12346',country='Norwayland')]));old,_=poll(s);self.assertEqual(1,len(s._next['rows']))
        install(s,export([row(),row('12346')]));new,_=poll(s,old);install(s);missing,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],missing['seen'])
        s=source(countries=['Missing']);install(s)
        with self.assertRaises(SourceError):s.read_records()

    def test_invalid_metadata_and_mid_read_changes_preserve_saved_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for kw in [{'second':export([row(nature='Correction')])},{'stamp':'2026-09-13'},{'stamp':'2026-08-01'},{'stamp':'2026-09-16'},{'second_stamp':'2026-09-15'}]:
            install(s,**kw)
            with tempfile.TemporaryDirectory() as d:
                store=StateStore(d);store.save('research',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s);self.assertIn('research',result.errors);self.assertEqual(saved,store.load('research'))

    def test_invalid_dates_identifiers_type_and_access(self):
        for k,v in [('RetractionDate','2/30/2026 0:00'),('OriginalPaperDate','12/8/2024 12:00'),('RetractionDOI','10.1234/bad%ZZ'),('RetractionDOI','bad'),('RetractionPubMedID','-1'),('RetractionNature','Unknown type'),('Paywalled','Maybe')]:
            s=source();install(s,export([row(**{k:v})]))
            with self.assertRaises(SourceError):s.read_records()

    def test_transport_csv_and_size_limits(self):
        for b,options in [(b'bad\xff\n',{}),(b'"unterminated\n',{}),(export([row(),row('12346')]),{'max_records':1}),(export([row(),row('12346')]),{'max_export_records':1}),(export(trailer=1001),{}),(export([row(**{'Notes':'x'*11000})]),{})]:
            s=source(**options);install(s,b)
            with self.assertRaises(SourceError):s.read_records()
        s=source();r=response(b'',302);s.get=Mock(return_value=r)
        with self.assertRaises(SourceError):s.read_records()
        r.close.assert_called_once()
        s=source(max_download_bytes=1024);install(s,b'x'*1025)
        with self.assertRaises(SourceError):s.read_records()

    def test_configuration_rejects_removed_empty_selection_and_bad_bounds(self):
        for options in [{'events':['removed']},{'complete_snapshot':True},{'countries':[]},{'max_download_bytes':True},{'max_export_age_days':0}]:
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
