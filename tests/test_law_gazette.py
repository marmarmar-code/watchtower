import bz2
import copy
import io
import json
import tarfile
import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.common import SourceError
from watchtower.sources.law_gazette import LawGazetteSource, PAGE_URL, ARCHIVE_BASE
from test_change_sources import poll, response


def doc(kind='lov',number='1',title='Example law',year=2026,body='Announcement text'):
    ident=f'LTI/{kind}/{year}-01-23-{number}'
    legacy=f'{"LOV" if kind=="lov" else "FOR"}-{year}-01-23-{number}'
    return (f'<html><head><title>{title}</title></head><body><header class="documentHeader"><dl>'
        f'<dd class="dokid">{ident}</dd><dd class="refid">{ident[4:]}</dd><dd class="legacyID">{legacy}</dd>'
        f'<dd class="dateOfPublication">{year}-01-23 12:00</dd><dd class="dateInForce">Kongen bestemmer</dd>'
        f'<dd class="ministry">Example ministry</dd><dd class="journalNumber">{year}-1</dd>'
        f'<dd class="title">{title}</dd></dl></header>'
        f'<main class="documentBody" data-lovdata-URL="{ident}"><h1>{title}</h1>{body}</main></body></html>').encode()


def member(kind='lov',number='1',year=2026,**kwargs):
    return f'lti/{year}/{"nl" if kind=="lov" else "sf"}-{year}0123-{number}.xml',doc(kind,number,year=year,**kwargs)


def archive(members, *, tar_format=tarfile.USTAR_FORMAT, link=False):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w',format=tar_format) as tar:
        for name,raw in members:
            info=tarfile.TarInfo(name)
            if link:
                info.type=tarfile.SYMTYPE;info.linkname='/outside/file'
                tar.addfile(info)
            else:
                info.size=len(raw)
                if tar_format==tarfile.PAX_FORMAT:info.pax_headers={'comment':'Extended header'}
                tar.addfile(info,io.BytesIO(raw))
    return bz2.compress(out.getvalue())


def src(**options):
    return LawGazetteSource(SourceConfig(id='laws',kind='law_gazette',label='Gazette',urls=(),
        filters=FilterRule(match_all=True),options={'max_bytes':2_000_000,**options}))


def listing(raw,year=2026,stamp='2026-09-14T01:31:00Z'):
    return [{'filename':f'lovtidend-avd1-{year}.tar.bz2','description':'Annual archive',
             'sizeBytes':str(len(raw)),'lastModified':stamp}]


def install(source,members,second=None,*,year=2026):
    first=archive(members);other=archive(members if second is None else second)
    source.get=Mock(side_effect=[response(listing(first,year)),response(first),
                                response(listing(other,year)),response(other)])


class LawGazetteTests(unittest.TestCase):
    def test_real_pipeline_baseline_new_changed_and_repeat(self):
        s=src();initial=[member(),member('forskrift','2')];install(s,initial)
        state,alerts=poll(s);self.assertEqual([],alerts)
        self.assertEqual([PAGE_URL,ARCHIVE_BASE+'lovtidend-avd1-2026.tar.bz2']*2,
                         [c.args[0] for c in s.get.call_args_list])
        install(s,list(reversed(initial)));repeat,alerts=poll(s,state)
        self.assertEqual(state,repeat);self.assertEqual([],alerts)
        changed=[member(body='Changed announcement'),member('forskrift','2'),member(number='3')]
        install(s,changed);next_state,alerts=poll(s,repeat)
        self.assertEqual(['added','changed'],sorted(a.item.metadata['event'] for a in alerts))
        details=' '.join(' '.join(a.item.alert_details) for a in alerts)
        self.assertIn('Tekst eller ressursreferanser',details)
        self.assertIn('Kongen bestemmer',details)
        install(s,changed);same,alerts=poll(s,next_state)
        self.assertEqual(next_state,same);self.assertEqual([],alerts)

    def test_legal_metadata_and_structured_references_are_monitored(self):
        s=src();base=[member(body='<article data-change-part="law/1">Same text</article>')]
        install(s,base);state,_=poll(s)
        name,raw=member(body='<article data-change-part="law/2">Same text</article>')
        raw=raw.replace(b'Kongen bestemmer',b'2026-12-01')
        install(s,[(name,raw)]);_,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));detail=' '.join(alerts[0].item.alert_details)
        self.assertIn('2026-12-01',detail);self.assertIn('ressursreferanser',detail)
        self.assertFalse(any(len(word)==64 and all(c in 'abcdef0123456789' for c in word) for word in detail.split()))

    def test_formatting_only_and_optional_metadata_absence(self):
        s=src();install(s,[member(body='<p>Same text</p>')]);state,_=poll(s)
        install(s,[member(body='<p style="color: blue">Same   text</p>')]);same,alerts=poll(s,state)
        self.assertEqual([],alerts);self.assertEqual(state,same)
        fields=next(iter(same['source_state']['records']['rows'].values()))['row']['fields']
        self.assertIsNone(fields['basedOn'])

    def test_all_documents_validated_before_type_filter(self):
        s=src(document_types=['lov']);install(s,[member(),member('forskrift','2')])
        self.assertEqual(1,len(s.read_records()))
        name,raw=member('forskrift','2');raw=raw.replace(b'FOR-2026',b'FOR-2025')
        install(s,[member(),(name,raw)])
        with self.assertRaises(SourceError):s.read_records()

    def test_header_date_identity_and_duplicate_rejection(self):
        name,raw=member()
        invalid=[raw.replace(b'2026-01-23 12:00',b'2026-02-30 12:00'),
                 raw.replace(b'<dd class="dateInForce">Kongen bestemmer</dd>',b''),
                 raw.replace(b'<title>Example law</title>',b'<title>Other</title>'),
                 raw.replace(b'<dd class="refid">',b'<dd class="dokid">'),
                 raw.replace(b'data-lovdata-URL="LTI/lov/2026-01-23-1"',b'data-lovdata-URL="LTI/lov/2026-01-23-9"')]
        for bad in invalid:
            with self.subTest(bad=bad[:25]):
                with self.assertRaises(SourceError):src()._archive(archive([(name,bad)]),2026)
        with self.assertRaises(SourceError):src()._archive(archive([member(),member()]),2026)
        other='lti/2026/nl-20260123-01.xml'
        with self.assertRaises(SourceError):src()._archive(archive([member(),(other,raw)]),2026)

    def test_archive_boundaries_and_member_safety(self):
        ordinary=archive([member()]);expanded=bz2.decompress(ordinary)
        for raw in (ordinary[:-4],ordinary+ordinary,bz2.compress(expanded[:1024]),
                    bz2.compress(expanded+b'x'*512+b'\0'*1024),archive([]),
                    archive([member()],link=True),archive([member()],tar_format=tarfile.PAX_FORMAT),
                    archive([('../outside.xml',doc())])):
            with self.subTest(size=len(raw)):
                with self.assertRaises(SourceError):src()._archive(raw,2026)
        with self.assertRaises(SourceError):src(max_expanded_bytes=1024)._archive(ordinary,2026)
        with self.assertRaises(SourceError):src(max_document_bytes=1024)._archive(archive([member(body='x'*2048)]),2026)
        with self.assertRaises(SourceError):src(max_archive_records=1)._archive(archive([member(),member(number='2')]),2026)

    def test_inconsistent_second_sweep_preserves_history(self):
        s=src();install(s,[member()]);state,_=poll(s);saved=copy.deepcopy(state)
        install(s,[member()],[member(body='different')])
        with self.assertRaises(SourceError):poll(s,state)
        self.assertEqual(saved,state)
        a=archive([member()]);s.get=Mock(side_effect=[response(listing(a)),response(a),
            response(listing(a,stamp='2026-09-15T01:31:00Z')),response(a)])
        with self.assertRaises(SourceError):poll(s,state)
        self.assertEqual(saved,state)

    def test_list_schema_size_and_transport_limits(self):
        a=archive([member()]);s=src()
        for data in ([],listing(a)*2,[dict(listing(a)[0],filename='../unsafe')],
                     [dict(listing(a)[0],sizeBytes=123)],[dict(listing(a)[0],lastModified='invalid')]):
            with self.assertRaises(SourceError):s._selection(json.dumps(data).encode())
        wrong=listing(a);wrong[0]['sizeBytes']=str(len(a)+1)
        s.get=Mock(side_effect=[response(wrong),response(a)])
        with self.assertRaises(SourceError):s.read_records()
        for body,status in ((b'x'*2048,200),(b'[]',302)):
            s=src(max_bytes=1024);r=response(body,status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
            self.assertFalse(s.get.call_args.kwargs['allow_redirects'])

    def test_year_regression_and_no_withdrawal_inference(self):
        s=src();install(s,[member()]);state,_=poll(s)
        install(s,[member(number='2')]);_,alerts=poll(s,state)
        self.assertEqual(['added'],[a.item.metadata['event'] for a in alerts])
        install(s,[member(year=2025)],year=2025)
        with self.assertRaises(SourceError):poll(s,state)
        for opts in ({'complete_snapshot':True},{'document_types':['unknown']},{'latest_years':0}):
            with self.assertRaises(ValueError):src(**opts)


if __name__=='__main__':
    unittest.main()
