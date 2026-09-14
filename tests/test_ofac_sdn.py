import hashlib
import io
import json
import unittest
import zipfile
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.ofac_sdn import OfacSdnSource, DATA_URL, NS
from test_change_sources import poll, response


def entry(uid='1', typ='Entity', name='Example Company', programs=('TEST',), details=''):
    return (f'<sdnEntry><uid>{uid}</uid><lastName>{name}</lastName><sdnType>{typ}</sdnType>'
            '<programList>'+''.join(f'<program>{p}</program>' for p in programs)+'</programList>'+details+'</sdnEntry>')


def xml(entries, count=None, publish='09/10/2026'):
    return (f'<sdnList xmlns="{NS[1:-1]}"><publshInformation><Publish_Date>{publish}</Publish_Date>'
            f'<Record_Count>{len(entries) if count is None else count}</Record_Count></publshInformation>'
            +''.join(entries)+'</sdnList>').encode()


def archive(raw, extra=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('SDN.XML', raw)
        if extra:
            z.writestr('other.xml', raw)
    return buffer.getvalue()


class OfacTests(unittest.TestCase):
    def source(self, **options):
        cfg = SourceConfig(id='ofac', kind='ofac_sdn', label='OFAC', urls=(DATA_URL,),
                           filters=FilterRule(match_all=True), options={'entry_types':['Entity','Vessel'], **options})
        return OfacSdnSource(cfg, timeout=1, retry_attempts=1)

    def load(self, source, raw, extra=False):
        meta = [{'fileName':'SDN.XML', 'size':len(raw), 'hashCodes':json.dumps({'SHA-256':hashlib.sha256(raw).hexdigest()})}]
        zipped = archive(raw, extra)
        meta.append({'fileName':'SDN_XML.ZIP', 'size':len(zipped),
                     'downloadLink':'Published/11111111-1111-1111-1111-111111111111/2026-09-10/22222222-2222-2222-2222-222222222222/SDN_XML.ZIP'})
        source.post = Mock(return_value=response(meta))
        source.get = Mock(return_value=response(zipped))
        return meta

    def test_baseline_entry_and_detail_reordering_are_quiet(self):
        a='<aka><uid>10</uid><type>a.k.a.</type><category>strong</category><lastName>Example Alias</lastName></aka>'
        b=a.replace('10','11').replace('Example Alias','Second Alias')
        s=self.source(); rows=[entry(details='<akaList>'+a+b+'</akaList>',programs=('TEST','OTHER')),entry('2','Vessel')]
        self.load(s,xml(rows)); first,alerts=poll(s); self.assertEqual([],alerts)
        rows=[entry('2','Vessel'),entry(details='<akaList>'+b+a+'</akaList>',programs=('OTHER','TEST'))]
        self.load(s,xml(rows,publish='09/11/2026')); second,alerts=poll(s,first)
        self.assertEqual([],alerts); self.assertEqual(first,second)

    def test_substantial_revision_keeps_uid_and_emits_once(self):
        s=self.source();self.load(s,xml([entry()]));state,_=poll(s)
        self.load(s,xml([entry(name='Revised Company',programs=('OTHER',),details='<remarks>Revised note</remarks>')]))
        state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual(['1'],list(s._next['rows']))
        self.assertIn('OTHER',' '.join(alerts[0].item.alert_details));_,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_explicit_type_program_selection_and_all_programs_retained(self):
        s=self.source(entry_types=['Entity'],programs=['TEST'])
        self.load(s,xml([entry(programs=('TEST','OTHER')),entry('2','Vessel'),entry('3','Individual'),entry('4',programs=('OTHER',))]))
        rows=s.read_records();self.assertEqual(['1'],[r['key'] for r in rows]);self.assertEqual(['OTHER','TEST'],rows[0]['fields']['programs'])

    def test_details_preserve_addresses_ids_remarks_and_vessel_data(self):
        detail='<idList><id><uid>4</uid><idType>Example ID</idType><idNumber>00012</idNumber></id></idList><addressList><address><uid>5</uid><city>Example City</city></address></addressList><remarks>Full note</remarks><vesselInfo><vesselFlag>Example Flag</vesselFlag></vesselInfo>'
        s=self.source();self.load(s,xml([entry(typ='Vessel',details=detail)]));f=s.read_records()[0]['fields']
        self.assertEqual('00012',f['ids'][0]['idNumber']);self.assertEqual('Full note',f['remarks']);self.assertEqual('Example City',f['addresses'][0]['city']);self.assertEqual('Example Flag',f['vessel_info']['vesselFlag'])

    def test_bad_identity_duplicate_unselected_uid_and_count_rejected(self):
        for raw in [xml([entry('x')]),xml([entry('0')]),xml([entry(),entry('1','Individual')]),xml([entry()],count=2),xml([entry()],count='bad'),xml([entry('1','Unknown')])]:
            with self.subTest(raw=raw):
                s=self.source();self.load(s,raw)
                with self.assertRaises(SourceError):s.read_records()

    def test_schema_namespace_and_child_duplicates_rejected(self):
        detail='<addressList><address><uid>5</uid><city>A</city></address><address><uid>5</uid><city>B</city></address></addressList>'
        for raw in [xml([entry(details=detail)]),xml([entry()]).replace(b'<lastName>',b'<lastName xmlns="urn:wrong">'),xml([entry()]).replace(b'<uid>1</uid>',b'<uid>1</uid><uid>2</uid>'),xml([entry()]).replace(b'</sdnEntry>',b'<unknown>x</unknown></sdnEntry>'),xml([entry()]).replace(b'<lastName>Example Company</lastName>',b'')]:
            s=self.source();self.load(s,raw)
            with self.assertRaises(SourceError):s.read_records()

    def test_metadata_hash_missing_duplicate_and_size_rejected(self):
        for change in ['hash','missing','duplicate','size']:
            s=self.source();meta=self.load(s,xml([entry()]))
            if change=='hash':meta[0]['hashCodes']=json.dumps({'SHA-256':'0'*64})
            if change=='missing':meta[0]['hashCodes']=None
            if change=='duplicate':meta.append(meta[0])
            if change=='size':meta[0]['size']+=1
            s.post.return_value=response(meta)
            with self.subTest(change=change),self.assertRaises(SourceError):s.read_records()

    def test_zip_and_xml_bounds_and_declarations_rejected(self):
        s=self.source();self.load(s,xml([entry()]),extra=True)
        with self.assertRaises(SourceError):s.read_records()
        s=self.source(max_xml_bytes=1024);self.load(s,xml([entry(name='X'*2000)]))
        with self.assertRaises(SourceError):s.read_records()
        s.get.assert_not_called()
        for raw in [b'<',b'<!DOCTYPE sdnList []>'+xml([entry()]),xml([entry()]).decode().encode('utf-16')]:
            s=self.source();self.load(s,raw)
            with self.assertRaises(SourceError):s.read_records()

    def test_response_bounds_and_redirects_close_responses(self):
        for location in ['get','post']:
            s=self.source();self.load(s,xml([entry()]));reply=response(b'',status=302,headers={'Location':'https://example.test/'})
            getattr(s,location).return_value=reply
            with self.assertRaises(SourceError):s.read_records()
            reply.close.assert_called_once()
        s=self.source(max_bytes=1024);self.load(s,xml([entry()]));s.get.return_value=response(b'x'*1025)
        with self.assertRaises(SourceError):s.read_records()
        self.assertFalse(s.get.call_args.kwargs['allow_redirects'])
        s=self.source();self.load(s,xml([entry()]));s.post.return_value=response(b'x'*1000001)
        with self.assertRaises(SourceError):s.read_records()

    def test_missing_selected_rows_preserves_state_and_absence_is_not_removal(self):
        s=self.source();self.load(s,xml([entry(),entry('2')]));state,_=poll(s)
        self.load(s,xml([entry('2')]));_,alerts=poll(s,state);self.assertEqual([],alerts)
        self.load(s,xml([entry('3','Individual')]))
        with self.assertRaises(SourceError):poll(s,state)

    def test_only_metadata_bound_official_storage_redirect_is_followed(self):
        s=self.source();raw=xml([entry()]);meta=self.load(s,raw)
        url='https://wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com/'+meta[1]['downloadLink']+'?temporary=example'
        for target in [url,url.replace('SDN_XML.ZIP','OTHER.ZIP'),url.replace('https://','https://user@'),url.replace('.amazonaws.com/','.amazonaws.com.example.test/')]:
            s.get=Mock(side_effect=[response(b'',status=302,headers={'Location':target}),response(archive(raw))])
            if target==url:
                self.assertEqual(1,len(s.read_records()));self.assertEqual(2,s.get.call_count)
            else:
                with self.assertRaises(SourceError):s.read_records()
                self.assertEqual(1,s.get.call_count)

    def test_configuration_and_record_limits(self):
        for options in [{'entry_types':['Individual']},{'entry_types':[]},{'programs':'TEST'},{'programs':[]},{'complete_snapshot':True},{'events':['removed']},{'max_xml_bytes':60000001}]:
            with self.subTest(options=options),self.assertRaises(ValueError):self.source(**options)
        for options in [{'max_records':1},{'max_register_records':1}]:
            s=self.source(**options);self.load(s,xml([entry(),entry('2')]))
            with self.assertRaises(SourceError):s.read_records()


if __name__ == '__main__':
    unittest.main()
