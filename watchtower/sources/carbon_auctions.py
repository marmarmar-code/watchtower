"""Published EU ETS1 auction results with explicit source units and workbook dates."""
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal,InvalidOperation,ROUND_FLOOR
from io import BytesIO
import re
from zipfile import ZipFile,BadZipFile
from xml.etree.ElementTree import ParseError

from .changes import SnapshotSource,canonical,integer,strings
from .common import SourceError
from .workbooks import NS,REL,RID,_xml,_rows,column_index

URL='https://public.eex-group.com/eex/eua-auction-report/emission-spot-primary-market-auction-report-2026-data.xlsx'
INFO='https://www.eex.com/en/markets/environmental-markets/emissions-auctions'
FORMULA=f'HYPERLINK("{INFO}","{INFO}")'
HEADERS=['Date','Time','Auction Name','Contract','Status','Auction Price €/tCO2','Minimum Bid €/tCO2','Maximum Bid €/tCO2','Mean €/tCO2','Median €/tCO2','Auction Volume tCO2','Total Amount of Bids','Number of bids submitted','Number of successful bids','Average number of bids per bidder','Average bid size','Average volume bid per bidder','Standard deviation of bid volume per bidder','Average volume won per bidder','Standard deviation of volume won per bidder','Cover Ratio','Total Number of Bidders','Number of Successful Bidders','Total Revenue €','Zone']
BENEFICIARIES=['Austria\n(AT)','Belgium\n(BE)','Bulgaria\n(BG)','Cyprus\n(CY)','Czech Republic\n(CZ)','Germany\n(DE)','Denmark\n(DK)','Estonia\n(EE)','Greece\n(EL)','Spain\n(ES)','Finland\n(FI)','France\n(FR)','Croatia\n(HR)','Hungary\n(HU)','Ireland\n(IE)','Innovation Fund\n(IF)','Iceland\n(IS)','Italy\n(IT)','InnoFund RRF\n(IX)','Liechtenstein\n(LI)','Lithuania\n(LT)','Luxembourg\n(LU)','Latvia\n(LV)','Modernisation Fund\n(MF)','Malta\n(MT)','MS RRF\n(MX)','Netherlands\n(NL)','Norway\n(NO)','Poland\n(PL)','Portugal\n(PT)','Romania\n(RO)','Sweden\n(SE)','Social Climate Fund\n(SF)','Slovenia\n(SI)','Slovakia\n(SK)']


def amount(raw,optional=False):
    if raw=='' and optional:return None
    if len(raw)>60 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,2})?',raw):raise SourceError('Auction number is invalid')
    try:
        n=Decimal(raw)
        if not n.is_finite() or n.adjusted()>16 or n.as_tuple().exponent< -15 or len(n.as_tuple().digits)>25:raise ValueError()
        s=format(n,'f');return '0' if not n else s.rstrip('0').rstrip('.') if '.' in s else s
    except (InvalidOperation,ValueError) as exc:raise SourceError('Auction number exceeds bounds') from exc


class CarbonAuctionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(URL,)):raise ValueError('carbon_auctions currently accepts the official 2026 report')
        if self.complete or 'removed' in self.events:raise ValueError('Annual report absence cannot prove auction cancellation')
        self.max_rows=integer(config.options.get('max_rows',1000),'max_rows',7,3000)
        self.max_unpacked=integer(config.options.get('max_unpacked_bytes',2000000),'max_unpacked_bytes',1024,10000000)
        self.zones=strings(config.options['zones'],'zones') if 'zones' in config.options else []
        if any(not re.fullmatch(r'[A-Z]{2}',x) for x in self.zones):raise ValueError('zones must be exact two-letter source labels')
        self.field_labels={'auction_date':'Auksjonsdato','source_time':'Kildens klokke (hh:mm)','auction_name':'Auksjonsnavn','contract':'Kontrakt','status':'Kildestatus','price_eur_per_tco2':'Pris (EUR/tCO2)','volume_tco2':'Volum (tCO2)','total_revenue_eur':'Inntekt (EUR)','beneficiary_revenue_eur':'Fordelt inntekt (EUR)',**self.field_labels}

    def _table(self,raw):
        try:
            with ZipFile(BytesIO(raw)) as z:
                infos=z.infolist()
                if not 1<=len(infos)<=100 or len({x.filename for x in infos})!=len(infos) or sum(x.file_size for x in infos)>self.max_unpacked or any(x.flag_bits&1 or x.filename.startswith('/') or '..' in x.filename.split('/') for x in infos):raise SourceError('Auction workbook exceeds archive bounds')
                wb=_xml(z.read('xl/workbook.xml'));sheets=wb.findall(NS+'sheets/'+NS+'sheet');props=wb.findall(NS+'workbookPr')
                if wb.tag!=NS+'workbook' or len(sheets)!=1 or sheets[0].get('name')!='Primary Market Auction' or len(props)!=1 or props[0].get('date1904') not in {'false','0'}:raise SourceError('Auction workbook identity or date system changed')
                rels=_xml(z.read('xl/_rels/workbook.xml.rels'));rr=rels.findall(REL+'Relationship');chosen=[r for r in rr if r.get('Id')==sheets[0].get(RID)]
                if rels.tag!=REL+'Relationships' or len({r.get('Id') for r in rr})!=len(rr) or len(chosen)!=1 or chosen[0].get('Target')!='worksheets/sheet1.xml' or chosen[0].get('TargetMode')=='External':raise SourceError('Auction sheet relationship changed')
                shared_root=_xml(z.read('xl/sharedStrings.xml'));shared=[''.join(t.text or '' for t in item.iter(NS+'t')) for item in shared_root.findall(NS+'si')]
                if shared_root.tag!=NS+'sst':raise SourceError('Auction shared-string namespace changed')
                styles=_xml(z.read('xl/styles.xml'));formats={x.get('numFmtId'):x.get('formatCode') for x in styles.findall(NS+'numFmts/'+NS+'numFmt')};xfs=styles.findall(NS+'cellXfs/'+NS+'xf')
                if styles.tag!=NS+'styleSheet' or len(formats)!=len(styles.findall(NS+'numFmts/'+NS+'numFmt')):raise SourceError('Auction style metadata changed')
                sheet=_xml(z.read('xl/worksheets/sheet1.xml'));data=sheet.findall(NS+'sheetData')
                if sheet.tag!=NS+'worksheet' or len(data)!=1:raise SourceError('Auction worksheet identity changed')
                dimensions=sheet.findall(NS+'dimension')
                bound=re.fullmatch(r'B2:BI([0-9]+)',dimensions[0].get('ref','')) if len(dimensions)==1 else None
                if not bound or not 7<=int(bound[1])<=self.max_rows:raise SourceError('Auction worksheet dimension changed')
                formula_count=0;seen=set()
                for r in data[0]:
                    n=r.get('r','')
                    if r.tag!=NS+'row' or not n.isdigit() or not 2<=int(n)<=self.max_rows or n in seen:raise SourceError('Auction worksheet row is invalid or repeated')
                    seen.add(n);number=int(n);r.set('r',str(number-1))
                    for c in r:
                        ref=c.get('r','');column_index(ref,number);f=c.findall(NS+'f')
                        if f:
                            if ref!='D3' or len(f)!=1 or f[0].text!=FORMULA or f[0].attrib or c.find(NS+'v') is not None:raise SourceError('Auction workbook has an unexpected formula')
                            c.remove(f[0]);formula_count+=1
                        if number>=7 and ref in {'B'+n,'C'+n}:
                            style=c.get('s','')
                            if not style.isdigit() or int(style)>=len(xfs) or c.get('t','n')!='n':raise SourceError('Auction date/time cell type changed')
                            code=formats.get(xfs[int(style)].get('numFmtId'))
                            if code!=('dd.mm.yyyy' if ref.startswith('B') else 'hh:mm'):raise SourceError('Auction date/time display format changed')
                        c.set('r',re.sub(r'[0-9]+$',str(number-1),ref))
                if formula_count!=1:raise SourceError('Auction workbook information link changed')
                if seen!={str(n) for n in range(2,int(bound[1])+1)}:raise SourceError('Auction rows disagree with worksheet dimension')
                rows=_rows(sheet,shared,self.max_rows,61,allow_blank_rows=True)
        except SourceError:raise
        except (BadZipFile,KeyError,ValueError,UnicodeError,RuntimeError,IndexError,ParseError) as exc:raise SourceError('Auction workbook is invalid') from exc
        # Row 1 is blank in the source; the compatibility view offsets indices only.
        expected=[{3:'Public'},{1:'More information'},{11:'EEX Emissions market / Primary Market Auction'},{1:'References',6:'Prices',11:'Volumes',22:'Participants',24:'Revenue',26:'Revenue'}]
        if len(rows)<6 or any({i:v for i,v in enumerate(r) if v}!=e for r,e in zip(rows[:4],expected)) or rows[4]!=['']+HEADERS+BENEFICIARIES:raise SourceError('Auction header or units changed')
        return rows[5:]

    def _records(self,raw):
        out=[];seen=set();zones=set()
        for row in self._table(raw):
            if row[0] or not all(row[1:6]) or not re.fullmatch(r'[A-Z]{2}',row[25]):raise SourceError('Auction identity is incomplete')
            r=row[1:26];serial=Decimal(amount(r[0]));clock=Decimal(amount(r[1]))
            if serial!=serial.to_integral_value() or not 46023<=serial<=46387 or int(clock)!=int(serial):raise SourceError('Auction date/time is outside the explicit report year or disagrees')
            day=(date(1899,12,30)+timedelta(days=int(serial))).isoformat()
            if not day.startswith('2026-'):raise SourceError('Auction year changed')
            minutes=int(((clock-serial)*1440).to_integral_value(rounding=ROUND_FLOOR));time=f'{minutes//60:02d}:{minutes%60:02d}'
            name,contract,status,zone=r[2],r[3],r[4],r[24]
            if len(name)>150 or not re.fullmatch(r'[A-Z0-9]{2,10}',contract) or len(status)>80:raise SourceError('Auction descriptive fields changed')
            key=canonical([day,name,contract,zone])
            if key in seen:raise SourceError('Auction identity repeats')
            seen.add(key);zones.add(zone)
            numeric={HEADERS[i]:amount(r[i],True) for i in range(5,24)}
            if status=='successful' and any(numeric[k] is None for k in ['Auction Price €/tCO2','Auction Volume tCO2','Total Revenue €']):raise SourceError('Successful auction lacks its result')
            f={'auction_date':day,'source_time':time,'auction_name':name,'contract':contract,'status':status,'zone':zone,'price_eur_per_tco2':numeric.pop('Auction Price €/tCO2'),'volume_tco2':numeric.pop('Auction Volume tCO2'),'total_revenue_eur':numeric.pop('Total Revenue €'),'other_source_statistics':numeric,'beneficiary_revenue_eur':{h:amount(v,True) for h,v in zip(BENEFICIARIES,row[26:])}}
            if not self.zones or zone in self.zones:out.append({'key':key,'title':name+' · '+day,'url':URL,'published':None,'source_time_excel_serial':str(clock),'fields':f})
        if not seen or set(self.zones)-zones or len(out)>self.max_records:raise SourceError('Auction selection is absent or exceeds bounds')
        return sorted(out,key=lambda r:r['key'])

    def _download(self):
        response=self.get(URL,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:raise SourceError('Auction report redirected unexpectedly')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('Auction report exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:response.close()

    def read_records(self):
        a=self._records(self._download());b=self._records(self._download())
        if a!=b:raise SourceError('Auction report changed between complete reads')
        return a

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields'];info=('Nyobservert kvoteauksjon' if event=='added' else 'Endret publisert auksjonsresultat',f['auction_name']+' · '+f['auction_date'],f['contract']+' · '+f['status'],'Pris: '+(f['price_eur_per_tco2'] or 'ikke oppgitt')+' EUR/tCO2','Volum: '+(f['volume_tco2'] or 'ikke oppgitt')+' tCO2')
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        return replace(item,alert_details=info+('Publisert årsrapport for EU ETS1. Ingen antatt tidssone, publiseringstid eller sanntidspris; blanke beløp er ikke null.',))
