from datetime import date
import unittest

from watchtower.config import load_config
from watchtower.setup import PRESETS, make_config


class SectorPackTests(unittest.TestCase):
    def config(self, preset):
        import tempfile
        from pathlib import Path

        content = make_config(preset, ["Synthetic Topic"], [], "teams")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchtower.toml"
            path.write_text(content, encoding="utf-8")
            return load_config(path)

    def test_requested_sector_packs_are_available_and_buildable(self):
        self.assertTrue({"media", "energy", "food", "legal", "communications"} <= set(PRESETS))
        for preset in PRESETS:
            with self.subTest(preset=preset):
                config = self.config(preset)
                self.assertGreaterEqual(len(config.sources), 3)
                self.assertEqual(len(config.sources), len({source.id for source in config.sources}))

    def test_media_pack_has_full_unfiltered_industry_coverage(self):
        sources = self.config("media").sources
        sector_sources = [source for source in sources if source.id not in {
            "regjeringen", "stortinget", "konkurransetilsynet"
        }]
        self.assertEqual(15, len(sector_sources))
        self.assertTrue(all(source.filters.match_all for source in sector_sources))

    def test_existing_packs_keep_topic_filtering(self):
        for preset in ("general", "finance", "health", "digital", "property", "retail"):
            with self.subTest(preset=preset):
                sources = self.config(preset).sources
                self.assertTrue(all(
                    source.filters.match_all or source.filters.include_any == ("Synthetic Topic",)
                    for source in sources
                ))

    def test_broad_additions_to_existing_packs_use_the_existing_topic_scope(self):
        expected = {
            "general": {"arbeidstilsynet_news"},
            "health": {"fhi_news_1"},
            "digital": {"datatilsynet_news", "nsm_news"},
            "property": {"kartverket_news"},
            "retail": {"landbruksdir_news", "fiskeridir_news", "tolletaten_news"},
        }
        for preset, ids in expected.items():
            by_id = {source.id: source for source in self.config(preset).sources}
            with self.subTest(preset=preset):
                self.assertTrue(ids <= set(by_id))
                self.assertTrue(all(
                    by_id[source_id].filters.include_any == ("Synthetic Topic",)
                    and not by_id[source_id].filters.match_all
                    for source_id in ids
                ))

    def test_existing_pack_additions_also_inherit_entity_scope(self):
        import tempfile
        from pathlib import Path

        content = make_config(
            "general", ["Synthetic Topic"], ["123456785=Synthetic Enterprise"], "teams"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchtower.toml"
            path.write_text(content, encoding="utf-8")
            by_id = {source.id: source for source in load_config(path).sources}
        self.assertIn("Synthetic Enterprise", by_id["arbeidstilsynet_news"].filters.include_any)

    def test_lottery_news_remains_optional_instead_of_filling_the_finance_pack(self):
        self.assertNotIn("lottstift_news", {source.id for source in self.config("finance").sources})

    def test_overlapping_scopes_are_optional_in_sector_packs(self):
        energy = {source.id for source in self.config("energy").sources}
        food = {source.id for source in self.config("food").sources}
        health = {source.id for source in self.config("health").sources}
        self.assertNotIn("nve_news", energy)
        self.assertNotIn("fiskeridir_aquaculture", food)
        self.assertNotIn("fiskeridir_fisheries", food)
        self.assertNotIn("nye_metoder_decision_docs", health)

    def test_verification_dates_are_real_iso_dates_not_future_placeholders(self):
        from watchtower.recipes import load_recipes
        from watchtower.rss_profiles import load_profiles

        for row in [*load_profiles(), *load_recipes()]:
            checked = date.fromisoformat(row["verified_on"])
            self.assertLessEqual(checked, date.today())


if __name__ == "__main__":
    unittest.main()
