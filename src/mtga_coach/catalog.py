"""Read only the installed Arena card database. No network or game modification."""
import html
import re
import sqlite3
from pathlib import Path

from .config import default_card_database

COLORS = {"1": "W", "2": "U", "3": "B", "4": "R", "5": "G"}
# `Cards.Types` codes, confirmed against the installed database by joining TypeTextId.
TYPE_CODES = {"1": "Artifact", "2": "Creature", "3": "Enchantment", "4": "Instant",
              "5": "Land", "8": "Planeswalker", "10": "Sorcery", "11": "Kindred",
              "14": "Battle"}
LAND_CODE = "5"
# `Cards.Rarity` codes, confirmed by counting the installed database against known cards.
RARITY_CODES = {0: "none", 1: "basic", 2: "common", 3: "uncommon", 4: "rare", 5: "mythic"}
# Wildcards are spent per rarity; basics and tokens cost nothing.
WILDCARD_RARITIES = ("common", "uncommon", "rare", "mythic")


def clean_text(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def missing_card(cid):
    return {"id": cid, "name": f"Carta #{cid}", "name_en": "", "text": "",
            "mana_cost": "", "mana_value": None, "mana_tokens": [], "type_line": "ID não resolvido",
            "colors": [], "color_identity": [], "is_land": False, "power": "", "toughness": "",
            "resolved": False, "set": "", "collector_number": "", "rarity": "",
            "is_token": False, "rebalanced": False, "linked_faces": []}


def mana_symbols(text):
    # Arena mana tokens: o1, oU, oW/U, oX. Preserve unknown token text.
    return re.findall(r"o([^o]+)", text or "")


def _codes(value):
    return [item for item in (value or "").split(",") if item]


def resolve_cards(ids, card_database_path=None):
    ids = list(dict.fromkeys(int(i) for i in ids if int(i) > 0))
    result = {cid: missing_card(cid) for cid in ids}
    path = Path(card_database_path) if card_database_path else default_card_database()
    if not path or not path.is_file() or not ids:
        return result
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return result
    con.row_factory = sqlite3.Row
    localization_cache = {}

    def loc(lid, language="ptBR"):
        if not lid:
            return ""
        key = (int(lid), language)
        if key not in localization_cache:
            table = "Localizations_ptBR" if language == "ptBR" else "Localizations_enUS"
            row = con.execute(f"SELECT Loc FROM {table} WHERE LocId=? "
                              "ORDER BY CASE Formatted WHEN 1 THEN 0 WHEN 0 THEN 1 ELSE 2 END LIMIT 1",
                              (int(lid),)).fetchone()
            localization_cache[key] = clean_text(row[0]) if row else ""
        return localization_cache[key]

    try:
        for cid in ids:
            row = con.execute("SELECT * FROM Cards WHERE GrpId=?", (cid,)).fetchone()
            if row is None:
                continue
            row = dict(row)
            name = loc(row.get("TitleId"))
            name_en = loc(row.get("TitleId"), "enUS")
            ability_texts = []
            for pair in (row.get("AbilityIds") or "").split(","):
                parts = pair.split(":")
                if len(parts) > 1 and parts[1].isdigit():
                    text = loc(int(parts[1])) or loc(int(parts[1]), "enUS")
                    if text and text not in ability_texts:
                        ability_texts.append(text.replace("CARDNAME", name or name_en))
            symbols = mana_symbols(row.get("OldSchoolManaText"))
            value = sum(int(x) if x.isdigit() else (0 if x in ("X", "Y", "Z") else 1)
                        for x in symbols)
            type_line = loc(row.get("TypeTextId")) or loc(row.get("TypeTextId"), "enUS")
            subtype = loc(row.get("SubtypeTextId")) or loc(row.get("SubtypeTextId"), "enUS")
            types = _codes(row.get("Types"))
            is_land = LAND_CODE in types
            colors = [COLORS[x] for x in _codes(row.get("Colors")) if x in COLORS]
            identity = [COLORS[x] for x in _codes(row.get("ColorIdentity")) if x in COLORS]
            result[cid] = {
                "id": cid, "name": name or name_en or f"Carta #{cid}", "name_en": name_en,
                "text": "\n".join(ability_texts), "mana_cost": "".join("{" + t + "}" for t in symbols),
                "mana_value": value, "mana_tokens": symbols,
                "type_line": type_line + (" — " + subtype if subtype else ""),
                # A land has no colour of its own; the mana it can produce is its colour
                # identity, which is what a mana-base count has to read.
                "colors": identity if is_land else colors,
                "color_identity": identity, "is_land": is_land,
                "type_codes": [TYPE_CODES.get(code, code) for code in types],
                "power": row.get("Power") or "", "toughness": row.get("Toughness") or "",
                "resolved": bool(name or name_en), "set": row.get("ExpansionCode") or "",
                "collector_number": row.get("CollectorNumber") or "",
                "rarity": RARITY_CODES.get(row.get("Rarity"), ""),
                "is_token": bool(row.get("IsToken")),
                "rebalanced": bool(row.get("IsRebalanced")),
                "linked_faces": [int(x) for x in (row.get("LinkedFaceGrpIds") or "").split(",") if x.isdigit()],
            }
    except sqlite3.Error:
        # A client schema change leaves unresolved entries visible and does not stop imports.
        pass
    finally:
        con.close()
    return result


def wildcard_cost(entries):
    """Wildcards a list of {'card', 'quantity'} entries would cost, by rarity.

    It counts what the list requires, not what is missing: Arena stopped publishing the
    owned collection in the log, so the app cannot know which copies are already owned.
    """
    cost = {rarity: 0 for rarity in WILDCARD_RARITIES}
    unresolved = 0
    for entry in entries:
        card = entry.get("card") or {}
        quantity = int(entry.get("quantity", 0))
        if not card.get("resolved"):
            unresolved += quantity
            continue
        rarity = card.get("rarity")
        if rarity in cost:
            cost[rarity] += quantity
    return {"cost": cost, "unresolved": unresolved}
