from __future__ import annotations

import unittest

from watchtower.incidents import summarize_sources


class IncidentSummaryTests(unittest.TestCase):
    def test_compacts_many_account_figures_but_keeps_other_sources(self):
        sources = [f"account_figures_{number}_v1" for number in range(55)]
        sources += ["nrk_editorial_vacancies_v1", "storting_recent_vote_discovery"]
        summary = summarize_sources(sources)
        self.assertEqual(
            "BRREG-regnskapstall (55 kilder), nrk_editorial_vacancies_v1, storting_recent_vote_discovery",
            summary,
        )
        self.assertNotIn("account_figures_0", summary)

    def test_preserves_small_incidents_and_limits_unrelated_ids(self):
        self.assertEqual("ingen", summarize_sources([]))
        self.assertEqual("account_figures_a", summarize_sources(["account_figures_a"]))
        self.assertEqual("a, b", summarize_sources(["b", "a", "a"]))
        self.assertEqual("a, b + 2 andre", summarize_sources(["d", "c", "b", "a"], max_names=2))


if __name__ == "__main__":
    unittest.main()
