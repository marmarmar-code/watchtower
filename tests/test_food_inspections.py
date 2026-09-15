import unittest
from unittest.mock import Mock
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.food_inspections import FoodInspectionsSource
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

HTML = '''<div><h1>Restaurant</h1><div>Orgnr. 914322863</div></div>
<div data-select_element_id="Z123_TilsynAvtale"><div title="Resultat fra kilden"></div><div>12.02.25</div></div>
<div id="Z123_TilsynAvtale"><p>Mattilsynets vurdering.</p></div>'''

def source():
    return FoodInspectionsSource(SourceConfig(id='inspection', kind='food_inspections', label='Inspection',
        urls=('https://smilefjes.mattilsynet.no/spisested/trondheim/restaurant.Z123/',),
        filters=FilterRule(match_all=True), options={'organisation_number':'914322863'}))

class FoodInspectionTests(unittest.TestCase):
    def test_result_change_uses_stable_inspection_identity(self):
        s=source();s.get=Mock(return_value=response(HTML.encode()));state,alerts=poll(s)
        self.assertEqual([],alerts)
        s.get.return_value=response(HTML.replace('Resultat fra kilden','Endret resultat fra kilden').encode())
        state,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertIsNone(alerts[0].item.published)
        self.assertIn('Endret resultat',str(alerts[0].item.alert_details))
        _,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_rejects_wrong_organisation_bad_date_missing_panel_and_duplicates(self):
        for raw in (HTML.replace('914322863','974100169'), HTML.replace('12.02.25','31.02.25'),
                    HTML.replace('id="Z123_TilsynAvtale"','id="missing"'),HTML+HTML):
            with self.subTest(raw=raw),self.assertRaises(SourceError):
                s=source();s.get=Mock(return_value=response(raw.encode()));s.read_records()

    def test_future_inspection_date_fails_closed(self):
        with self.assertRaises(SourceError):
            s=source();s.get=Mock(return_value=response(HTML.replace('12.02.25','12.02.68').encode()));s.read_records()
