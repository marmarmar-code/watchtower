"""Verified Norwegian numbered KRT-1500 forms; no reporter contact extraction."""
from datetime import datetime
import re
from .common import SourceError

NOTE = 'Dersom det er foretatt mer enn én transaksjon av samme type, finansielle instrument, dag og handelsplass, så kan pris og volum angis for hver transaksjon.'
LABELS = {
    '1.3.1': 'Jeg rapporterer som / på vegne av:',
    '1.4.2': 'Fullt navn', '1.4.4': 'Stilling/Rolle',
    '1.5.2': 'Fullt navn', '1.7.1': 'Fullt navn', '1.7.2': 'Stilling/Rolle',
    '2.1': 'Er dette en ny melding eller korrigering av tidligere meldt transaksjon?',
    '2.1.1': 'Beskrivelse av korrigering',
    '2.2.1': 'Lei-kode (til utsteder eller deltaker på utslippskvotemarked)',
    '2.2.2': 'Foretaksnavn (til utsteder eller deltaker på utslippskvotemarked)',
    '2.3.1': 'Instrument :', '2.3.2': 'ISIN-kode :', '2.3.2.1': 'Utstedernavn :',
    '2.4.1': 'Transaksjonstype :',
    '2.5.1': 'Er transaksjonen knyttet til utøvelse av et aksjeopsjonsprogram? :',
    '2.6.1': 'Valuta :', '2.8.1': 'Gjennomsnittlig pris per enhet :',
    '2.8.2': 'Aggregert volum :', '2.8.3': 'Total sum :',
    '2.9.1': 'Angi dato :', '2.10.1': 'Handelsplass :', '2.11': 'Kommentar',
}


def parse_krt(pdf):
    text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
    if 'KRT-1500 Skjema for melding om transaksjoner utført av personer med ledelsesansvar' not in ' '.join(text.split()):
        raise SourceError('KRT-1500 heading changed')
    if len(text) > 100000 or '\ufffd' in text:
        raise SourceError('KRT text is excessive or undecodable')
    refs = re.findall(r'Referansenummer: ([a-f0-9]{12})(?:\s|$)', text)
    if len(refs) != 1:
        raise SourceError('KRT form reference is absent or ambiguous')
    marks = list(re.finditer(r'(?m)^(\d+(?:\.\d+)*) ', text))
    values = {}
    for i, mark in enumerate(marks):
        number = mark[1]
        if number in values:
            raise SourceError('KRT field repeated; inspect multiple-transaction layout')
        values[number] = ' '.join(text[mark.end():marks[i+1].start() if i+1 < len(marks) else len(text)].split())
    # Unknown financial fields must not silently disappear from a parsed transaction.
    if any(n.startswith('2.') and n not in LABELS and n not in ('2.2', '2.3') for n in values):
        raise SourceError('KRT transaction field set changed')

    def value(number, optional=False):
        if optional and number not in values:
            return None
        actual = values.get(number, '')
        label = LABELS[number]
        if not actual.startswith(label + ' '):
            raise SourceError('KRT numbered field label or value changed: ' + number)
        result = actual[len(label):].strip()
        if number == '2.10.1':
            if not result.endswith(' ' + NOTE):
                raise SourceError('KRT transaction note boundary changed')
            result = result[:-len(NOTE)].strip()
        if not result or len(result) > 10000:
            raise SourceError('KRT field is empty or excessive')
        return result

    capacity = value('1.3.1')
    if capacity == 'Primærinnsider':
        actor, role, related = value('1.4.2'), value('1.4.4'), None
        if any(n in values for n in ('1.5.2', '1.7.1', '1.7.2')):
            raise SourceError('KRT actor capacity fields are inconsistent')
    elif capacity == 'Nærstående person':
        actor, role, related = value('1.5.2'), value('1.7.2'), value('1.7.1')
        if any(n in values for n in ('1.4.2', '1.4.4')):
            raise SourceError('KRT actor capacity fields are inconsistent')
    else:
        raise SourceError('KRT actor capacity layout is not verified')
    lei, isin, currency = value('2.2.1'), value('2.3.2'), value('2.6.1')
    if not re.fullmatch(r'[A-Z0-9]{20}', lei) or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', isin) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise SourceError('KRT LEI, ISIN or currency format changed')
    stamp = value('2.9.1')
    try:
        if not re.fullmatch(r'\d\d\.\d\d\.\d{4}', stamp):
            raise ValueError()
        datetime.strptime(stamp, '%d.%m.%Y')
    except ValueError as exc:
        raise SourceError('KRT transaction date is invalid') from exc
    notification = value('2.1')
    correction = value('2.1.1', True)
    if notification not in ('Ny melding', 'Korrigering') or (notification == 'Korrigering') != bool(correction):
        raise SourceError('KRT notification and correction description are inconsistent')
    price, volume, total = value('2.8.1'), value('2.8.2'), value('2.8.3')
    comment = value('2.11')
    return [{'section': 1, 'actor_text': actor, 'capacity_text': capacity + '; ' + role,
             'related_insider_text': related, 'notification_text': notification,
             'correction_text': correction, 'issuer_text': value('2.2.2'), 'lei': lei,
             'instrument_text': value('2.3.1') + '; ISIN: ' + isin + '; ' + value('2.3.2.1'),
             'transaction_type_text': value('2.4.1'), 'currency_text': currency,
             'option_program_text': value('2.5.1'),
             'price_volume_text': 'Gjennomsnittlig pris per enhet: ' + price + '; Valuta: ' + currency,
             'aggregate_text': 'Aggregert volum: ' + volume + '; Total sum: ' + total + '; Valuta: ' + currency,
             'transaction_date_text': stamp, 'venue_text': value('2.10.1'),
             'form_reference': refs[0],
             'comment_text': None if comment == 'Du har ikke lagt inn informasjon her' else comment}]
