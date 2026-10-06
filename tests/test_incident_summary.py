from __future__ import annotations

import unittest

from watchtower.incidents import summarize_sources


class IncidentSummaryTests(unittest.TestCase):
    def test_compacts_many_account_figures_but_keeps_other_sources(self):
        sources = [f"account_figures_{900000000 + number}_v1" for number in range(55)]
        sources += ["nrk_editorial_vacancies_v1", "storting_recent_vote_discovery"]
        summary = summarize_sources(sources)
        self.assertEqual(
            "BRREG-regnskapstall (55 kilder), nrk_editorial_vacancies_v1, storting_recent_vote_discovery",
            summary,
        )
        self.assertNotIn("account_figures_900000000", summary)

    def test_preserves_small_incidents_and_limits_unrelated_ids(self):
        self.assertEqual("ingen", summarize_sources([]))
        self.assertEqual("account_figures_a", summarize_sources(["account_figures_a"]))
        self.assertEqual("a, b", summarize_sources(["b", "a", "a"]))
        self.assertEqual("a, b + 2 andre", summarize_sources(["d", "c", "b", "a"], max_names=2))
        self.assertEqual(
            "account documents (4 kilder)",
            summarize_sources([f"account_documents_{900000000+i}_v2" for i in range(4)]),
        )
        self.assertEqual(
            "account_figures_a",
            summarize_sources(["account_figures_a"]),
        )



    def test_coverage_changes_are_reported_only_on_transition(self):
        from watchtower.incidents import coverage_transitions
        before = {"warnings": {"doffin": ["result_window_full"], "other": ["partial"]}}
        same = {"warnings": {"doffin": ["result_window_full"], "other": ["partial"]}}
        self.assertEqual(({}, {}), coverage_transitions(before, same))
        after = {"warnings": {"doffin": ["result_window_full", "no_overlap_with_previous_window"]}}
        self.assertEqual(
            ({"doffin": ("no_overlap_with_previous_window",)}, {"other": ("partial",)}),
            coverage_transitions(before, after),
        )

    def test_escalate_after_24_hours_only_once(self):
        from watchtower.incidents import reportable_escalations
        common = {
            "errors": {"account_figures_a": "SourceError: failed"},
            "error_since": {"account_figures_a": "2026-10-05T10:00:00+00:00"},
        }
        before = dict(common, last_run_at="2026-10-06T09:45:00+00:00")
        current = dict(common, last_run_at="2026-10-06T10:05:00+00:00")
        self.assertEqual(
            ("account_figures_a",),
            reportable_escalations(before, current, now="2026-10-06T10:05:00+00:00"),
        )
        self.assertEqual((), reportable_escalations(
            current, current, now="2026-10-06T11:05:00+00:00"))


if __name__ == "__main__":
    unittest.main()
