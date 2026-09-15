from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.consultations import ConsultationsSource
from watchtower.sources.common import SourceError
from test_change_sources import poll

BASE = 'https://www.regjeringen.no/no/dokumenter/example/id1234567/'
UID = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'


def page(body='', heading='Example consultation', base=BASE):
    return f'<meta property="og:url" content="{base}"><main><header class="article-header"><h1>{heading}</h1></header>{body}</main>'


def metadata(deadline='06.10.2026', status='På høring'):
    return page(f'<div class="horing-meta"><p><strong>Status:</strong>{status}</p><p><strong>Høringsfrist:</strong>{deadline}</p></div>')


def listing(rows=None, count=None):
    rows = [(UID, 'Public body', 'Annen offentlig etat')] if rows is None else rows
    entries = ''.join(f'<li id="{uid}" data-instans="{category}"><a href="?uid={uid}">{name}</a></li>' for uid, name, category in rows)
    return page(f'<form id="searchPageNavigationSearchForm" action="{BASE}"><input name="consterm"><select name="horingssvar_filter"><option selected value="?showSvar=true">Alle høringsinstanser</option></select><div class="results"><span class="count">Søket ditt gav {len(rows) if count is None else count} treff.</span></div><ul data-horingssvar-list>{entries}</ul></form>')


def detail(body='Example text', kind='Med merknad', name='Public body', attachment=''):
    return page(f'<div class="hearing-answer"><p class="hearing-answer-timestamp"><b>Dato:</b> 14.08.2026</p><div><strong>Svartype:</strong> {kind}</div><div class="article-body">{body}</div>{attachment}</div>', 'Høringssvar fra ' + name)


def reply(html, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, iter_content=Mock(return_value=[html.encode()]), close=Mock())


def source(**options):
    return ConsultationsSource(SourceConfig(id='consultations', kind='consultations', label='Example consultation', urls=(BASE,), filters=FilterRule(match_all=True), options={'allow_empty': True, **options}))


def sequence(s, index=None, answer=None, end=None):
    index = listing() if index is None else index
    s.get = Mock(side_effect=[reply(metadata()), reply(index), *([reply(answer or detail())] if answer is not False else []), reply(index if end is None else end)])


class ConsultationTests(TestCase):
    def test_metadata_repeat_deadline_and_status_changes(self):
        s=source(); s.get=Mock(return_value=reply(metadata())); state,alerts=poll(s); self.assertEqual([],alerts)
        same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)
        s.get.return_value=reply(metadata('07.10.2026','Under behandling')); state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts)); text=' '.join(alerts[0].item.alert_details)
        self.assertIn('2026-10-06 → 2026-10-07',text); self.assertIn('På høring → Under behandling',text)
        self.assertIn('ikke et vedtaksutfall',text)
        same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)

    def test_response_repeat_body_attachment_and_type_changes(self):
        s=source(profile='responses'); sequence(s); state,alerts=poll(s); self.assertEqual([],alerts)
        sequence(s,answer=detail('  Example   text ')); same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)
        attachment=f'<ul class="link-list"><li><a href="{BASE}Download/?vedleggId={OTHER}">Example.pdf</a></li></ul>'
        sequence(s,answer=detail('Updated text',attachment=attachment)); state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts)); text=' '.join(alerts[0].item.alert_details)
        self.assertIn('svartekst er endret',text); self.assertIn('vedleggstitler er endret',text); self.assertNotIn('Updated text',text)
        sequence(s,answer=detail('',kind='Uten merknad')); state,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        self.assertIn('Med merknad → Uten merknad',' '.join(alerts[0].item.alert_details))
        sequence(s,answer=detail('',kind='Uten merknad')); same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)

    def test_new_response_and_absence_never_claim_removal(self):
        s=source(profile='responses'); sequence(s,index=listing([]),answer=False); state,alerts=poll(s); self.assertEqual([],alerts)
        sequence(s); state,alerts=poll(s,state); self.assertEqual(1,len(alerts)); self.assertEqual('added',alerts[0].item.metadata['event'])
        sequence(s,index=listing([]),answer=False); _,alerts=poll(s,state); self.assertEqual([],alerts)

    def test_private_people_excluded_after_validating_entire_list(self):
        index=listing([(UID,'Public body','Annen offentlig etat'),(OTHER,'Example person','Privatperson')])
        s=source(profile='responses'); sequence(s,index=index); state,_=poll(s)
        self.assertEqual(1,len(state['source_state']['records']['rows'])); self.assertEqual(4,s.get.call_count)
        self.assertFalse(any(OTHER in call.args[0] for call in s.get.call_args_list))
        s=source(profile='responses',exclude_private_people=False)
        s.get=Mock(side_effect=[reply(metadata()),reply(index),reply(detail()),reply(detail(name='Example person')),reply(index)])
        self.assertEqual(2,len(s.read_records()))

    def test_partial_filtered_duplicate_and_racing_lists_are_atomic(self):
        s=source(profile='responses'); sequence(s); state,_=poll(s); saved=deepcopy(state); saved_next=deepcopy(s._next)
        bad=[listing(count=2), listing([(UID,'Public body','Agency')]*2), listing().replace('name="consterm"','name="consterm" value="filter"'), listing().replace('?showSvar=true','?showSvar=true&amp;horingssvar_filter=Agency'),listing().replace('?uid='+UID,'?uid='+OTHER)]
        for index in bad:
            sequence(s,index=index)
            with self.subTest(index=index),self.assertRaises(SourceError):poll(s,state)
            self.assertEqual(saved,state); self.assertEqual(saved_next,s._next)
        sequence(s,end=listing([]))
        with self.assertRaisesRegex(SourceError,'changed during'):poll(s,state)
        self.assertEqual(saved,state); self.assertEqual(saved_next,s._next)

    def test_response_identity_dates_types_and_attachment_scope(self):
        s=source(profile='responses')
        bad=[detail(name='Wrong body'), detail(kind='Unknown'), detail('',kind='Med merknad'), detail().replace('14.08.2026','31.02.2026'), detail(attachment='<ul class="link-list"><li><a href="https://example.test/file.pdf">Example</a></li></ul>'),detail().replace('/id1234567/','/id7654321/')]
        for answer in bad:
            sequence(s,answer=answer)
            with self.subTest(answer=answer),self.assertRaises(SourceError):s.read_records()
        for html in [metadata('31.02.2026'),page(),metadata().replace('Status:','Unknown:')]:
            s=source();s.get=Mock(return_value=reply(html))
            with self.assertRaises(SourceError):s.read_records()

    def test_bounds_and_redirects_close_responses(self):
        s=source(max_bytes=1024); r=reply('x'*1025); s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        for location in ['https://example.test/no/dokumenter/example/id1234567/',BASE+'?uid='+UID,BASE.replace('1234567','7654321')]:
            s=source();r=reply('',302,{'Location':location});s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        s=source(profile='responses',max_list_entries=1);sequence(s,index=listing(count=2))
        with self.assertRaisesRegex(SourceError,'max_list_entries'):s.read_records()
        s=source(profile='responses',max_responses=1);sequence(s,index=listing([(UID,'A','Agency'),(OTHER,'B','Agency')]))
        with self.assertRaisesRegex(SourceError,'max_responses'):s.read_records()
        for options in [{'complete_snapshot':True},{'profile':'unknown'},{'exclude_private_people':'yes'},{'max_responses':False}]:
            with self.assertRaises(ValueError):source(**options)
