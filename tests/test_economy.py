"""The prize arithmetic. The table is typed in; the maths on top of it is not."""

import unittest

from mtga_coach import economy


class OutcomeTest(unittest.TestCase):
    def test_the_distribution_is_a_distribution(self):
        for event, table in economy.EVENTS.items():
            with self.subTest(event=event):
                outcomes = economy.outcome_probabilities(0.55, table["wins_cap"], table["losses_cap"])
                self.assertAlmostEqual(sum(outcomes.values()), 1.0, places=9)

    def test_every_run_stops_at_the_published_record(self):
        table = economy.EVENTS["QuickDraft"]
        for (wins, losses) in economy.outcome_probabilities(0.5, table["wins_cap"], table["losses_cap"]):
            self.assertTrue(wins == table["wins_cap"] or losses == table["losses_cap"])

    def test_a_player_who_never_wins_ends_at_zero_and_three(self):
        outcomes = economy.outcome_probabilities(0.0, 7, 3)
        self.assertEqual(outcomes, {(0, 3): 1.0})

    def test_a_player_who_never_loses_ends_at_seven(self):
        outcomes = economy.outcome_probabilities(1.0, 7, 3)
        self.assertEqual(outcomes, {(7, 0): 1.0})


class ReturnTest(unittest.TestCase):
    def test_the_return_rises_with_the_win_rate(self):
        values = [economy.expected_return("QuickDraft", rate)["expected_gems"]
                  for rate in (0.3, 0.45, 0.6, 0.75)]
        self.assertEqual(values, sorted(values))

    def test_break_even_is_the_rate_where_the_entry_comes_back(self):
        rate = economy.break_even("QuickDraft")
        self.assertIsNotNone(rate)
        below = economy.verdict("QuickDraft", rate - 0.02)["net_gems"]
        above = economy.verdict("QuickDraft", rate + 0.02)["net_gems"]
        self.assertLess(below, 0)
        self.assertGreater(above, 0)

    def test_an_unknown_event_computes_nothing(self):
        answer = economy.verdict("SomeFutureQueue", 0.55)
        self.assertFalse(answer["known"])
        self.assertIn("nothing is computed", answer["note"])

    def test_the_packs_that_come_with_the_entry_count_as_return(self):
        # Leaving them out is the classic way to make every draft look like a loss.
        answer = economy.verdict("PremierDraft", 0.5)
        bare = answer["expected_gems"] + answer["expected_packs"] * economy.PACK_GEMS
        self.assertGreater(answer["returned_gems_equivalent"], bare)


if __name__ == "__main__":
    unittest.main()


class MeasuredPayoutTest(unittest.TestCase):
    """A row the wallet paid replaces a row this app typed in."""

    def test_a_measured_row_changes_the_expected_return(self):
        typed = economy.expected_return("QuickDraft", 0.6)["expected_gems"]
        richer = economy.expected_return("QuickDraft", 0.6, measured={7: 5000})["expected_gems"]
        self.assertGreater(richer, typed)

    def test_a_measured_row_moves_the_break_even_rate(self):
        typed = economy.break_even("QuickDraft")
        poorer = economy.break_even("QuickDraft", measured={7: 300, 6: 300, 5: 300})
        self.assertGreater(poorer, typed)

    def test_an_event_that_can_never_pay_for_itself_has_no_break_even(self):
        # Not zero and not an exception: there is no rate at which it comes back.
        self.assertIsNone(economy.break_even(
            "QuickDraft", measured={wins: 0 for wins in range(8)}))

    def test_a_measurement_equal_to_the_typed_row_changes_nothing(self):
        # This is the case that verifies the table instead of correcting it.
        same = economy.EVENTS["QuickDraft"]["prizes"][7]["gems"]
        self.assertEqual(economy.break_even("QuickDraft", measured={7: same}),
                         economy.break_even("QuickDraft"))

    def test_each_record_says_whether_it_was_measured(self):
        answer = economy.expected_return("QuickDraft", 0.5, measured={7: 950})
        measured = [row for row in answer["records"] if row["measured"]]
        self.assertTrue(measured)
        self.assertTrue(all(row["wins"] == 7 for row in measured))
        self.assertEqual(answer["measured_wins"], [7])
