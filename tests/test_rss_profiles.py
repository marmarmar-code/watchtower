from pathlib import Path
import tempfile
import unittest

from watchtower.rss_profiles import load_profiles, resolve_profile_urls


class RssProfileCatalogTests(unittest.TestCase):
    def test_official_profiles_have_required_metadata(self):
        profiles = load_profiles()
        self.assertEqual(
            {
                "politiloggen",
                "norges_bank_pressemeldinger",
                "finanstilsynet",
                "mattilsynet",
                "ema_news",
                "ema_human_medicines",
                "skatteetaten_uttalelser",
                "skatteklagenemnda",
                "nkom",
                "met_farevarsler",
                "lla_nyheter",
                "schibsted_media_news",
                "reuters_institute_news",
                "nordicom_news",
                "efj_news",
                "journalismfund_news",
                "fhi_news",
            },
            {p["id"] for p in profiles},
        )
        for profile in profiles:
            self.assertTrue(profile["official_url"].startswith("https://"))
            self.assertTrue(profile["coverage"])
            self.assertTrue(profile["feed_urls"])
            self.assertRegex(profile["verified_on"], r"^\d{4}-\d{2}-\d{2}$")

    def test_duplicate_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.toml"
            path.write_text(
                '[[profile]]\nid="same"\nname="A"\n'
                'official_url="https://a.test"\nowner="A"\n'
                'status="verified"\nverified_on="2026-08-27"\ncoverage="x"\n'
                'feed_urls=["https://a.test/feed"]\n'
                '[[profile]]\nid="same"\nname="B"\n'
                'official_url="https://b.test"\nowner="B"\n'
                'status="verified"\nverified_on="2026-08-27"\ncoverage="x"\n'
                'feed_urls=["https://b.test/feed"]\n'
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_profiles(path)

    def test_profile_ids_resolve_to_deduplicated_feed_urls(self):
        urls = resolve_profile_urls(["politiloggen", "politiloggen"])
        self.assertEqual(("https://api.politiloggen.politiet.no/feeds/rss",), urls)

    def test_unknown_profile_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown RSS profile"):
            resolve_profile_urls(["missing"])
