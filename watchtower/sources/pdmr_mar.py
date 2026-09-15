"""MAR form columns validated against the observed complete-page layouts."""
from datetime import datetime
import re
from .common import SourceError

SEQUENCE = ['1','a)','2','a)','b)','3','a)','b)','4','a)','b)','c)','d)','e)','f)']
KEYS = {1:'actor_text',3:'capacity_text',4:'notification_text',6:'issuer_text',7:'lei',
        9:'instrument_text',10:'transaction_type_text',11:'price_volume_text',
        12:'aggregate_text',13:'transaction_date_text',14:'venue_text'}
LABELS = {1: {'name'}, 3: {'position/status'}, 4: {'initialnotification/amendment'},
          6: {'name'}, 7: {'lei'},
          9: {'descriptionofthefinancialinstrument,typeofinstrumentidentificationcode',
              'descriptionofthefinancialinstrument,typeofinstrumentandidentificationcode'},
          10: {'natureofthetransaction'}, 11: {'price(s)andvolume(s)'},
          12: {'aggregatedinformation—aggregatedvolume—price',
               'aggregatedinformation-aggregatedvolume-price',
               'aggregatedinformationaggregatedvolumeaggregatedprice'},
          13: {'dateofthetransaction'}, 14: {'placeofthetransaction'}}


def clean(value):
    text = '\n'.join(' '.join(line.split()) for line in (value or '').splitlines() if line.strip())
    if not text or len(text) > 10000 or '\ufffd' in text:
        raise SourceError('MAR field is missing or undecodable')
    return text


def parse_mar(pdf):
    result = []
    for page in pdf.pages:
        words = page.extract_words()
        candidates = [w for w in words if re.fullmatch(r'[1-4](?:\.\d+)?|[a-f]\)', w['text'])]
        if not candidates:
            raise SourceError('MAR field markers are absent')
        left = min(w['x0'] for w in candidates)
        marks = sorted([w for w in candidates if abs(w['x0'] - left) < 6], key=lambda w:w['top'])
        if [re.sub(r'^4\.1$', '4', w['text']) for w in marks] != SEQUENCE:
            raise SourceError('MAR complete-page section sequence changed')
        line = sorted([w for w in words if abs(w['top'] - marks[1]['top']) < 3 and w['x0'] > marks[1]['x1']], key=lambda w:w['x0'])
        if len(line) < 2 or line[0]['text'] != 'Name':
            raise SourceError('MAR actor value column cannot be established')
        column = line[1]['x0'] - .5
        if not marks[1]['x1'] + 40 < column < page.width - 150:
            raise SourceError('MAR value column geometry changed')
        row = {'section': len(result)+1}
        for index, key in KEYS.items():
            mark = marks[index]
            top = mark['top'] - 4
            end = marks[index+1]['top'] - 4 if index+1 < len(marks) else page.height - 30
            band = page.filter(lambda o: o['object_type'] != 'char' or top <= (o['top']+o['bottom'])/2 < end)
            label = clean(band.crop((mark['x1']+1, top, column-1, end)).extract_text())
            if re.sub(r'\s', '', label).lower() not in LABELS[index]:
                raise SourceError('MAR field label changed: ' + key)
            value = clean(band.crop((column, top, page.width-30, end)).extract_text())
            row[key] = value
            if key == 'aggregate_text':
                # Preserve separate label/value columns; do not reinterpret aggregate price.
                row[key] = label + '\nValues:\n' + value
        if not re.fullmatch(r'[A-Z0-9]{20}', row['lei']):
            raise SourceError('MAR LEI is invalid')
        stamp = row['transaction_date_text']
        if not re.fullmatch(r'\d{4}-\d\d-\d\d(?:;(?: \d\d:\d\d CEST)?)?', stamp):
            raise SourceError('MAR transaction date format changed')
        try:
            datetime.strptime(stamp[:10], '%Y-%m-%d')
            if len(stamp) > 11:
                datetime.strptime(stamp[12:17], '%H:%M')
        except ValueError as exc:
            raise SourceError('MAR transaction date is invalid') from exc
        if len(row['venue_text']) > 200 or not row['price_volume_text'].startswith('Price(s)') or 'Volume(s)' not in row['price_volume_text']:
            raise SourceError('MAR price/volume headers or venue boundary changed')
        result.append(row)
    if not result:
        raise SourceError('MAR form has no complete transaction pages')
    return result
