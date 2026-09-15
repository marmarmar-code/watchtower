from copy import deepcopy
from html import escape
import tempfile
import unittest
from unittest.mock import Mock

from watchtower.config import Config, SourceConfig, FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.corporate_actions import CorporateActionsSource, URL, HEADERS
from watchtower.sources.common import SourceError
from test_change_sources import response, poll


def attachment(ident='101', aid='201', day='15/09/2026', name='example.pdf'):
    return f'<dl class="file-component"><ul class="file-component__header"><li>{day}</li><li>PDF</li></ul><a href="/en/listview/notice-download?id={ident}&amp;type=PDF&amp;attachmentId={aid}">{name}</a></dl>'


def row(ident='101', issued='14 Sep 2026', effect='To be Announced', action='Merger/Takeover', attachments=None):
    cells=[('noticenumber','Example '+ident),('noticedate',issued),('effect',effect),('noticename','<button>Toggle Visibility</button>'+escape(action)),('instruments','<button>Toggle Visibility</button>Example - Stock Option'),('',''),('','')]
    return f'<tbody id="row_ecap_{ident}"><tr class="row_{ident}">'+''.join(f'<td class="{cls}">{value}</td>' for cls,value in cells)+'</tr>'+''.join(f'<tr><td colspan="7"><div id="notice-{kind}-div-{ident}">'+((attachment(ident) if attachments is None else attachments) if kind=='download' else '')+'</div></td></tr>' for kind in ['abstract','detail','download'])+'</tbody>'


def page(*rows, number=1, next_page=None, footer=''):
    links=f'<li class="active"><a>{number}</a></li>'
    if next_page:
        links+=f'<li><a href="{URL}?alias=1&amp;pageSize=50&amp;pageNum={next_page}">Next</a></li>'
    return ('<html><body><input name="form_id" value="AwlNoticesPublicDerivativesFiltersForm_form"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in HEADERS)+'</tr></thead>'+''.join(rows or [row()])+'</table><ul class="pager pagination">'+links+'</ul>'+footer+'</body></html>').encode()


def source(**options):
    return CorporateActionsSource(SourceConfig(id='ca',kind='corporate_actions',label='Corporate actions',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s,*bodies):
    bodies=bodies or (page(),)
    s.get=Mock(side_effect=[response(b) for b in (*bodies,*bodies)])


class CorporateActionTests(unittest.TestCase):
    def test_baseline_repeat_and_separate_dates(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts)
        f=old['source_state']['records']['rows']['euronext:101']['row']['fields']
        self.assertEqual('2026-09-14',f['notice_date']);self.assertIsNone(f['effect_date']);self.assertEqual('To be Announced',f['effect_text'])
        self.assertEqual('2026-09-15',f['attachments'][0]['date']);self.assertEqual('Merger/Takeover',f['action']);self.assertEqual('Example - Stock Option',f['instrument'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_effective_date_and_revision_once(self):
        s=source();install(s);old,_=poll(s)
        body=page(row(effect='17 Sep 2026',action='Merger/Takeover (Updated)'))
        install(s,body);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        self.assertEqual('2026-09-17',new['source_state']['records']['rows']['euronext:101']['row']['fields']['effect_date'])
        install(s,body);self.assertEqual([],poll(s,new)[1])

    def test_attachment_correction_and_new_attachment(self):
        for files in [attachment(day='16/09/2026'),attachment(name='corrected.pdf'),attachment()+attachment(aid='202')]:
            s=source();install(s);old,_=poll(s);install(s,page(row(attachments=files)))
            new,alerts=poll(s,old);self.assertEqual(1,len(alerts));install(s,page(row(attachments=files)));self.assertEqual([],poll(s,new)[1])

    def test_new_notice_once_and_absence_not_cancellation(self):
        s=source();install(s);old,_=poll(s);both=page(row(),row('102'))
        install(s,both);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('added',alerts[0].item.metadata['event'])
        install(s,page());missing,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],missing['seen'])
        install(s,both);self.assertEqual([],poll(s,missing)[1])

    def test_order_footer_and_button_text_do_not_create_changes(self):
        s=source();a=attachment();b=attachment(aid='202');install(s,page(row(attachments=a+b),row('102')));old,_=poll(s)
        body=page(row('102'),row(attachments=b+a),footer='<p>Unrelated footer</p>').replace(b'Toggle Visibility',b'Updated control')
        install(s,body);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_two_page_window_and_window_size(self):
        s=source(max_pages=2);first=page(*(row(str(i)) for i in range(101,151)),next_page=2);second=page(row('151'),number=2)
        install(s,first,second);state,_=poll(s);self.assertEqual(51,len(state['source_state']['records']['rows']))
        self.assertIn('pageNum=2',s.get.call_args_list[1].args[0])
        s=source(max_pages=1);install(s,first);self.assertEqual(50,len(s.read_records()));self.assertEqual(2,s.get.call_count)

    def test_wrong_page_skips_duplicate_and_short_page_fail(self):
        full=page(*(row(str(i)) for i in range(101,151)),next_page=2)
        cases=[(full,page(row('151'),number=1)),(full,page(row(),number=2)),(full.replace(b'pageNum=2',b'pageNum=3'),),
               (page(row(),next_page=2),),(full.replace(b'pageSize=50',b'pageSize=25'),),
               (full.replace(URL.encode(),b'https://example.test/index'),)]
        for bodies in cases:
            s=source(max_pages=2);install(s,*bodies)
            with self.subTest(case=bodies[0][:30]),self.assertRaises(SourceError):s.read_records()

    def test_mid_read_drift_and_bad_snapshot_preserve_saved_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for bodies in [(page(),page(row(action='Changed'))),(page().replace(b'Issued',b'Other'),)]:
            s.get=Mock(side_effect=[response(x) for x in bodies])
            with tempfile.TemporaryDirectory() as directory:
                store=StateStore(directory);store.save('ca',old)
                outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('ca',outcome.errors);self.assertEqual(saved,store.load('ca'))

    def test_invalid_identity_dates_missing_metadata_and_headers(self):
        bads=[page(row(),row()),page(row(issued='31 Feb 2026')),page(row(effect='31 Feb 2026')),
              page(row(attachments=attachment(day='31/02/2026'))),page(row(attachments=attachment()+attachment())),
              page(row(attachments='')),page(row(action='')),page().replace(b'Issued',b'Changed'),
              page().replace(b'notice-detail-div-101',b'notice-detail-div-999'),page().replace(b'DerivativesFiltersForm',b'CashFiltersForm'),
              page().replace(b'<td class="noticedate">',b'<td class="noticedate" colspan="2">')]
        for body in bads:
            s=source();install(s,body)
            with self.subTest(body=body[:30]),self.assertRaises(SourceError):s.read_records()

    def test_attachment_urls_cannot_change_host_notice_type_or_identity(self):
        base=attachment()
        bads=[base.replace('id=101','id=999'),base.replace('type=PDF','type=HTML'),base.replace('attachmentId=201','attachmentId=0'),
              base.replace('/en/listview/notice-download','https://example.test/en/listview/notice-download'),
              base.replace('/en/listview/notice-download','https://live.euronext.com:443/en/listview/notice-download'),
              base.replace('attachmentId=201','attachmentId=201&amp;attachmentId=202'),base.replace('example.pdf','example.html')]
        for files in bads:
            s=source();install(s,page(row(attachments=files)))
            with self.subTest(files=files[:30]),self.assertRaises(SourceError):s.read_records()

    def test_transport_record_limits_and_config(self):
        for code,body,options in [(302,page(),{}),(200,page()[:-10],{}),(200,b'x'*2048,{'max_bytes':1024}),(200,page().replace(b'Example',b'\xff'),{})]:
            s=source(**options);r=response(body,status=code);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        s=source(max_records=1);install(s,page(row(),row('102')))
        with self.assertRaises(SourceError):s.read_records()
        for options in [{'scope':'cash'},{'page_size':True},{'page_size':25},{'max_pages':0},{'max_pages':6},{'max_pages':True},{'complete_snapshot':True},{'events':['removed']}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__': unittest.main()
