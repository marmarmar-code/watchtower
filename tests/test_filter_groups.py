from pathlib import Path
import tempfile
import unittest

from watchtower.config import FilterRule, load_config
from watchtower.engine import _matched_terms


class FilterGroupTests(unittest.TestCase):
    def load(self, rules, *, entity=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                entity + '\n[[source]]\nid="sample"\nkind="rss"\n[source.filter]\n' + rules,
                encoding="utf-8",
            )
            return load_config(path).sources[0]

    def test_context_and_event_alternatives_are_both_required(self):
        source = self.load('''
include_any_groups = [["Example Company", "Example Sector"], ["acquisition", "new director"]]
exclude_any = ["training invitation"]
''')
        for text in ("Example Company announces acquisition", "New director in Example Sector"):
            with self.subTest(text=text):
                self.assertTrue(source.filters.matches(text))
        for text in ("Example Company picnic", "Other sector acquisition", "Unrelated",
                     "Example Company acquisition training invitation"):
            with self.subTest(text=text):
                self.assertFalse(source.filters.matches(text))

    def test_old_clauses_keep_their_meaning_alongside_groups(self):
        rule = FilterRule(
            include_any=("alpha", "beta"), include_all=("confirmed",),
            exclude_any=("cancelled",), match_all=True,
            include_any_groups=(("acquisition", "appointment"),),
        )
        self.assertTrue(rule.matches("confirmed beta acquisition"))
        for text in ("confirmed beta", "confirmed acquisition", "beta acquisition",
                     "confirmed beta cancelled acquisition"):
            with self.subTest(text=text):
                self.assertFalse(rule.matches(text))
        self.assertTrue(FilterRule(match_all=True).matches("anything"))
        self.assertFalse(FilterRule().matches("anything"))

    def test_entity_aliases_supply_context_without_bypassing_event_group(self):
        source = self.load('''
entity_refs = ["sample"]
include_any_groups = [["acquisition", "new director"]]
''', entity='''
[[entity]]
id = "sample"
name = "Example Company"
aliases = ["Example Brand"]
''')
        self.assertTrue(source.filters.matches("Example Brand acquisition"))
        self.assertFalse(source.filters.matches("Example Brand picnic"))
        self.assertFalse(source.filters.matches("Other Brand acquisition"))
        self.assertEqual(("Example Brand", "acquisition"), _matched_terms(
            source, "Example Brand acquisition"
        ))

    def test_whole_words_unicode_and_phrase_whitespace(self):
        rule = FilterRule(
            include_any_groups=(("radio",), ("måne selskap",)), match_mode="whole_word"
        )
        self.assertTrue(rule.matches("RADIO: MA\u030aNE\u00a0\nSELSKAP"))
        self.assertFalse(rule.matches("Radiolocation from måne selskap"))
        self.assertFalse(rule.matches("SafetyRadio måne selskap"))
        self.assertFalse(rule.matches("radio måne selskaper"))

    def test_blank_and_malformed_groups_fail_closed(self):
        for value in ('"wrong"', '["flat"]', '[[]]', '[[" "]]', '[[1]]',
                      '[["valid"], []]'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(f"include_any_groups = {value}\nmatch_all = true")
        with self.assertRaises(ValueError):
            self.load("include_any_groups = []")

    def test_groups_trim_and_deduplicate_terms(self):
        source = self.load('include_any_groups = [[" alpha ", "alpha"], ["event"]]')
        self.assertEqual((("alpha",), ("event",)), source.filters.include_any_groups)
        self.assertEqual(("alpha", "event"), source.filters.matched_terms("alpha event"))


if __name__ == "__main__":
    unittest.main()
