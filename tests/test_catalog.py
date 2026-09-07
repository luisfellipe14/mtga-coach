import sqlite3
import tempfile
import unittest
from pathlib import Path

from mtga_coach import catalog
from mtga_coach.catalog import resolve_cards


def _in_language(language, ids, path):
    """Card text follows the Arena client's own localisation tables; English is the default."""
    original = catalog.LANGUAGE
    catalog.LANGUAGE = language
    try:
        return resolve_cards(ids, path)
    finally:
        catalog.LANGUAGE = original


class CatalogTests(unittest.TestCase):
    def test_resolves_local_text_and_preserves_missing_id(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cards.mtga'
            c = sqlite3.connect(path)
            c.executescript('''CREATE TABLE Cards (GrpId INT, TitleId INT, AbilityIds TEXT,
              OldSchoolManaText TEXT, TypeTextId INT, SubtypeTextId INT, Types TEXT, Colors TEXT,
              Power TEXT,Toughness TEXT,ExpansionCode TEXT,CollectorNumber TEXT,
              IsRebalanced INT,LinkedFaceGrpIds TEXT);
              CREATE TABLE Localizations_ptBR(LocId INT,Formatted INT,Loc TEXT);
              CREATE TABLE Localizations_enUS(LocId INT,Formatted INT,Loc TEXT);
              INSERT INTO Cards VALUES(10,1,'9:3','o1oU',2,0,'2','2','2','1','TST','10',0,'');
              INSERT INTO Localizations_ptBR VALUES(1,1,'Carta de teste'),(2,1,'Criatura'),(3,1,'Voar');
              INSERT INTO Localizations_enUS VALUES(1,1,'Test card'),(2,1,'Creature'),(3,1,'Flying');''')
            c.commit(); c.close()
            result = resolve_cards([10, 999], path)
            self.assertEqual(result[10]['name'], 'Test card')
            self.assertEqual(result[10]['mana_value'], 2)
            self.assertEqual(result[10]['colors'], ['U'])
            self.assertEqual(result[10]['text'], 'Flying')
            self.assertFalse(result[999]['resolved'])
            self.assertEqual(result[999]['id'], 999)
            self.assertEqual(_in_language('ptBR', [10], path)[10]['name'], 'Carta de teste')

    def test_missing_database_keeps_identifiers(self):
        card = resolve_cards([888], Path('missing-cards.mtga'))[888]
        self.assertFalse(card['resolved'])
        self.assertEqual(card['id'], 888)
