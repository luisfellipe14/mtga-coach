import unittest

from mtga_coach import analysis
from mtga_coach.catalog import wildcard_cost


def card(card_id, *, mana_value=1, colors=(), is_land=False, tokens=(), rarity="rare", name=None):
    """`tokens` mirrors the Arena mana string: "B", "1", "(R/G)" hybrid, "(B/P)" Phyrexian."""
    return {"id": card_id, "name": name or f"Carta {card_id}", "resolved": True,
            "mana_value": mana_value, "colors": list(colors), "is_land": is_land,
            "mana_tokens": list(tokens), "mana_cost": "".join("{" + t + "}" for t in tokens),
            "rarity": rarity}


class DrawMathTests(unittest.TestCase):
    def test_hypergeometric_matches_a_hand_computed_case(self):
        # Four copies in sixty, seven cards seen: 1 - C(56,7)/C(60,7).
        self.assertAlmostEqual(analysis.hypergeometric_at_least(1, 4, 60, 7), 0.39948, places=4)
        self.assertEqual(analysis.hypergeometric_at_least(0, 0, 60, 7), 1.0)
        self.assertEqual(analysis.hypergeometric_at_least(3, 2, 60, 7), 0.0)

    def test_rejects_a_draw_larger_than_the_library(self):
        with self.assertRaises(ValueError):
            analysis.hypergeometric_at_least(1, 4, 10, 11)

    def test_on_the_draw_sees_one_more_card_than_on_the_play(self):
        self.assertEqual(analysis.cards_seen(3, on_play=True), 9)
        self.assertEqual(analysis.cards_seen(3, on_play=False), 10)
        self.assertGreater(analysis.draw_probability(4, 60, 3, on_play=False),
                           analysis.draw_probability(4, 60, 3, on_play=True))


class SampleTests(unittest.TestCase):
    def test_wilson_interval_reproduces_the_published_worked_example(self):
        interval = analysis.wilson_interval(22, 40)
        self.assertAlmostEqual(interval["low"], 0.398, places=3)
        self.assertAlmostEqual(interval["high"], 0.693, places=3)
        self.assertEqual(interval["n"], 40)

    def test_an_empty_sample_has_no_interval_instead_of_a_full_range(self):
        self.assertIsNone(analysis.wilson_interval(0, 0))
        with self.assertRaises(ValueError):
            analysis.wilson_interval(5, 3)

    def test_separating_fifty_five_from_fifty_needs_over_a_thousand_games(self):
        needed = analysis.games_needed(0.50, 0.05)
        self.assertGreater(needed, 1500)
        self.assertLess(needed, 1600)


class ManaBaseTests(unittest.TestCase):
    def test_published_table_is_used_and_is_lower_than_the_hypergeometric_floor(self):
        published, exact = analysis.published_sources_needed(2, 3)
        self.assertEqual((published, exact), (18, True))
        self.assertGreater(analysis.sources_needed(2, 3), published)

    def test_a_turn_outside_the_table_is_answered_but_marked_inexact(self):
        value, exact = analysis.published_sources_needed(1, 9)
        self.assertEqual((value, exact), (12, False))
        self.assertEqual(analysis.published_sources_needed(9, 3), (None, False))

    def test_colour_requirement_names_the_card_that_sets_it_and_the_shortfall(self):
        entries = [{"card": card(1, mana_value=3, colors=("B",), tokens=("1", "B", "B"), name="Cárcere"), "quantity": 4},
                   {"card": card(2, mana_value=0, colors=("B",), is_land=True, rarity="basic"), "quantity": 10}]
        finding = next(item for item in analysis.colour_requirements(entries) if item["colour"] == "B")
        self.assertEqual((finding["pips"], finding["turn"], finding["needed"]), (2, 3, 18))
        self.assertEqual((finding["have"], finding["shortfall"]), (10, 8))
        self.assertEqual(finding["driver"], "Cárcere")
        self.assertEqual(finding["basis"], "karsten-2022")

    def test_hybrid_and_phyrexian_pips_do_not_demand_a_colour(self):
        entries = [
            {"card": card(1, mana_value=1, colors=("B",), tokens=("(B/P)",), name="Extração Cirúrgica"), "quantity": 4},
            {"card": card(2, mana_value=2, colors=("R", "G"), tokens=("1", "(R/G)"), name="Manamorfose"), "quantity": 4},
            {"card": card(3, mana_value=2, colors=("U",), tokens=("U", "U"), name="Contramágica"), "quantity": 4},
            {"card": card(4, mana_value=0, colors=("U",), is_land=True, rarity="basic"), "quantity": 12},
        ]
        findings = analysis.colour_requirements(entries)
        self.assertEqual([item["colour"] for item in findings], ["U"])
        self.assertEqual(findings[0]["pips"], 2)
        self.assertEqual(analysis.flexible_costs(entries), ["Extração Cirúrgica", "Manamorfose"])

    def test_curve_is_withheld_when_the_catalogue_missed_a_card(self):
        entries = [{"card": card(1, mana_value=2), "quantity": 4},
                   {"card": {"id": 9, "resolved": False}, "quantity": 2}]
        self.assertIsNone(analysis.mana_curve(entries))
        self.assertEqual(analysis.mana_curve(entries[:1]), {2: 4})

    def test_land_colours_come_from_identity_so_duals_count_for_both(self):
        entries = [{"card": card(1, colors=("U", "B"), is_land=True), "quantity": 4},
                   {"card": card(2, colors=("B",), is_land=True), "quantity": 6}]
        sources = analysis.colour_sources(entries)
        self.assertEqual(sources["by_colour"], {"B": 10, "U": 4})
        self.assertEqual(sources["total_lands"], 10)

    def test_remaining_library_never_reports_a_negative_count(self):
        self.assertEqual(analysis.remaining_library({10: 4, 11: 2}, {10: 4, 11: 3}), {})
        self.assertEqual(analysis.remaining_library({10: 4}, {10: 1}), {10: 3})


class WildcardTests(unittest.TestCase):
    def test_cost_counts_by_rarity_and_reports_what_it_could_not_resolve(self):
        entries = [{"card": card(1, rarity="rare"), "quantity": 4},
                   {"card": card(2, rarity="mythic"), "quantity": 2},
                   {"card": card(3, rarity="basic"), "quantity": 8},
                   {"card": {"id": 4, "resolved": False}, "quantity": 3}]
        result = wildcard_cost(entries)
        self.assertEqual(result["cost"], {"common": 0, "uncommon": 0, "rare": 4, "mythic": 2})
        self.assertEqual(result["unresolved"], 3)


if __name__ == "__main__":
    unittest.main()
