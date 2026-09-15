"""Generic in-memory PDF fixtures exercise actual PDF decoding and cell geometry."""
import unittest
from watchtower.sources.prac_signals import parse_pdf
from watchtower.sources.common import SourceError

DOC={'reference':'EMA/PRAC/12345/2026','meeting_text':'6-9 July 2026 PRAC meeting','meeting_start':'2026-07-06','meeting_end':'2026-07-09'}


def fixture(*,signal='Example signal',second_id='12345',grey='.882 .89 .949',header='Signal (EPITT No)',missing_summary=False,number='1.1.',reference=None):
    ref=reference or DOC['reference'];pages=[[],[],[]]
    def text(page,x,top,value,size=10,color='0 0 0'):
        value=value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        pages[page].append(f'BT /F1 {size} Tf {color} rg 1 0 0 1 {x} {841.89-top-size} Tm ({value}) Tj ET')
    def rect(page,x,top,width,height,color,stroke=False):
        pages[page].append(f'{color} rg {color} RG {x} {841.89-top-height} {width} {height} re '+('S' if stroke else 'f'))
    for n in range(3):
        text(n,62,780,'PRAC recommendations on signals',8);text(n,62,795,ref,8);text(n,485,795,f'Page {n+1}/3',8)
    text(0,62,100,'PRAC recommendations on signals',14)
    text(0,62,135,'Adopted at the 6-9 July 2026 PRAC meeting',12)
    text(0,62,165,'Generic contract fixture with no real medical information.')
    text(1,62,72,'1. Recommendations for update of the product information',12)
    text(1,62,105,number+' Example substance \x96 '+signal,12)
    labels=['Authorisation procedure','EPITT No','PRAC Rapporteur','Date of adoption'];values=['Example procedure','12345','Example role','9 July 2026']
    for n,(label,value) in enumerate(zip(labels,values)):
        rect(1,62,140+n*17,155,17,'0 0 0',True);rect(1,217,140+n*17,317,17,'0 0 0',True)
        text(1,67,142+n*17,label);text(1,222,142+n*17,value)
    text(1,62,225,'Recommendation',12);text(1,62,250,'Submit supplementary material within two months.')
    if not missing_summary:text(1,62,280,'Summary of product characteristics',12)
    text(1,62,310,'PROPOSED WORDING MUST NOT ENTER THE ACTION FIELD.')
    columns=[62,153,274,338,459,544]
    for category,top,ident in [(2,135,second_id),(3,430,'12345')]:
        title='2. Recommendations for submission of supplementary' if category==2 else '3. Other recommendations'
        text(2,62,top-45,title,12)
        if category==2:text(2,62,top-28,'information',12)
        headers=['INN',header,'PRAC|Rapporteur','Action for MAH','MAH'];cells=['Example|substance',signal+'|('+ident+')','Example|role','First action|Second action','Example|holder']
        for n in range(5):
            x=columns[n];width=columns[n+1]-x
            rect(2,x,top,width,35,'0 .2 .6');rect(2,x+.25,top+35,width-.5,62,grey)
            for k,line in enumerate(headers[n].split('|')):text(2,x+5,top+5+k*12,line,8,'1 1 1')
            for k,line in enumerate(cells[n].split('|')):
                # Repeated text-line backgrounds reproduce Word's nested rectangle layout.
                rect(2,x+5,top+40+k*13,width-10,12,grey)
                text(2,x+5,top+40+k*13,line,9)
    objects=[]
    def obj(value):objects.append(value);return len(objects)
    obj(b'<< /Type /Catalog /Pages 2 0 R >>');obj(b'')
    font=obj(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');kids=[]
    for commands in pages:
        stream=('\n'.join(commands)+'\n').encode('latin1');content=obj(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'endstream')
        kids.append(obj(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>'.encode()))
    objects[1]=('<< /Type /Pages /Count 3 /Kids ['+' '.join(f'{x} 0 R' for x in kids)+'] >>').encode()
    raw=bytearray(b'%PDF-1.4\n');offsets=[]
    for n,value in enumerate(objects,1):offsets.append(len(raw));raw.extend(f'{n} 0 obj\n'.encode()+value+b'\nendobj\n')
    xref=len(raw);raw.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:raw.extend(f'{offset:010d} 00000 n \n'.encode())
    raw.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode());return bytes(raw)


class PracPdfLayoutTests(unittest.TestCase):
    def test_real_pdf_decoder_preserves_three_categories_and_merged_cell_text(self):
        rows=parse_pdf(fixture(),DOC);self.assertEqual([1,2,3],[r['category'] for r in rows]);self.assertEqual(['12345']*3,[r['epitt'] for r in rows])
        self.assertEqual('First action Second action',rows[1]['action_text']);self.assertEqual('Example holder',rows[1]['mah_text']);self.assertIsNone(rows[1]['adoption_date'])
        self.assertEqual('2026-07-09',rows[0]['adoption_date']);self.assertEqual('Submit supplementary material within two months.',rows[0]['action_text']);self.assertNotIn('PROPOSED',str(rows))

    def test_changed_visible_signal_text_is_extracted(self):
        a=parse_pdf(fixture(),DOC);b=parse_pdf(fixture(signal='Changed signal'),DOC);self.assertNotEqual(a,b);self.assertEqual('Changed signal',b[1]['signal_text'])

    def test_unknown_fill_cannot_silently_drop_visible_signal_rows(self):
        with self.assertRaises(SourceError):parse_pdf(fixture(grey='.8 .8 .8'),DOC)

    def test_changed_column_heading_and_absent_wording_boundary_fail(self):
        for kw in [{'header':'Other heading'},{'missing_summary':True},{'number':'1.2.'},{'reference':'EMA/PRAC/99999/2026'}]:
            with self.subTest(kw=kw),self.assertRaises(SourceError):parse_pdf(fixture(**kw),DOC)

    def test_invalid_epitt_and_inconsistent_adoption_dates_fail(self):
        with self.assertRaises(SourceError):parse_pdf(fixture(second_id='bad'),DOC)
        with self.assertRaises(SourceError):parse_pdf(fixture(),{**DOC,'meeting_end':'2026-07-08'})

    def test_truncated_corrupt_or_non_pdf_and_page_cap_fail(self):
        for raw in [b'<html>error</html>',fixture()[:-10],b'%PDF-1.4\ninvalid\n%%EOF']:
            with self.assertRaises(SourceError):parse_pdf(raw,DOC)
        with self.assertRaises(SourceError):parse_pdf(fixture(),DOC,max_pages=2)


if __name__=='__main__':unittest.main()
