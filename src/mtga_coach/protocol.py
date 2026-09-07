"""GRE protocol vocabulary: annotation details, zone names and enum labels.

Only values observed in a real Arena log are translated here. An unknown value keeps
its raw form so the interface shows the gap instead of an invented label.
"""

ZONE_PREFIX = "ZoneType_"
ANNOTATION_PREFIX = "AnnotationType_"

# Zones whose contents are never rendered: transit and engine bookkeeping.
TRANSIENT_ZONES = ("Limbo", "Pending", "Suppressed")
# Zones whose card identity is hidden from every player by construction.
CONCEALED_ZONES = ("Library",)

ZONE_NAMES = {
    "Hand": "Hand", "Battlefield": "Battlefield", "Library": "Library",
    "Graveyard": "Graveyard", "Exile": "Exile", "Stack": "Stack",
    "Revealed": "Revealed", "Sideboard": "Sideboard", "Command": "Command",
    "Limbo": "Limbo", "Pending": "Pending", "Suppressed": "Suppressed",
}

PHASE_NAMES = {
    "Beginning": "Beginning", "Main1": "Main 1", "Combat": "Combat",
    "Main2": "Main 2", "Ending": "Ending",
}

STEP_NAMES = {
    "Untap": "Untap", "Upkeep": "Upkeep", "Draw": "Draw",
    "BeginCombat": "Begin combat", "DeclareAttack": "Declare attackers",
    "DeclareBlock": "Declare blockers", "CombatDamage": "Combat damage",
    "EndCombat": "End of combat", "End": "End step", "Cleanup": "Cleanup",
}

# AnnotationType_ZoneTransfer "category" detail, as written by the game engine.
TRANSFER_NAMES = {
    "Draw": "Drawn", "PlayLand": "Land played", "CastSpell": "Spell cast",
    "Resolve": "Resolved", "Countered": "Countered", "Discard": "Discarded",
    "Sacrifice": "Sacrificed", "Destroy": "Destroyed", "Exile": "Exiled",
    "Put": "Put into play", "Return": "Returned", "Conjure": "Conjured",
    "Mill": "Milled", "CardRevealed": "Revealed", "SBA_Damage": "Died to damage",
    "SBA_ZeroToughness": "Died to zero toughness", "SBA_LegendRule": "Legend rule",
    "SBA_Deathtouch": "Died to deathtouch", "SBA_ZeroLoyalty": "Zero loyalty",
    "Countered_Fizzle": "Countered on an illegal target", "MoveToStack": "Put on the stack",
}

# ActionType_* index used by AnnotationType_UserActionTaken's "actionType" detail.
USER_ACTION_TYPES = {
    1: "Cast", 2: "Activate", 3: "Play", 4: "Activate_Mana", 5: "Pass",
    6: "Activate_Special", 7: "Play_MDFC_Back", 8: "Cast_MDFC_Back",
}

MANA_COLOR_INDEX = {1: "W", 2: "U", 3: "B", 4: "R", 5: "G", 6: "C"}

RESULT_REASONS = {
    "ResultReason_Game": "Game ended", "ResultReason_Concede": "Concession",
    "ResultReason_Timeout": "Timeout", "ResultReason_Disconnect": "Disconnect",
    "ResultReason_Draw": "Draw", "ResultReason_Sideboard": "Sideboard",
}

MATCH_WIN_CONDITIONS = {
    "MatchWinCondition_SingleElimination": "BO1",
    "MatchWinCondition_Best_of_3": "BO3",
    "MatchWinCondition_BestOf3": "BO3",
    "MatchWinCondition_Best of 3": "BO3",
}


def zone_kind(zone):
    """Zone type without its protocol prefix, e.g. ZoneType_Hand -> Hand."""
    return str(zone.get("type", "")).removeprefix(ZONE_PREFIX)


def zone_label(kind):
    return ZONE_NAMES.get(kind, kind or "Unnamed zone")


def phase_label(phase, step):
    parts = [PHASE_NAMES.get(phase, phase), STEP_NAMES.get(step, step)]
    return " · ".join(part for part in parts if part)


def annotation_types(annotation):
    values = annotation.get("type", [])
    if isinstance(values, str):
        values = [values]
    return [str(value).removeprefix(ANNOTATION_PREFIX) for value in values]


def detail(annotation, key):
    """First value of a KeyValuePair detail, or None when the key is absent.

    Details carry the value under a type-specific field (valueInt32, valueString…);
    the caller gets the raw value and never a guessed default.
    """
    for item in annotation.get("details", []):
        if not isinstance(item, dict) or item.get("key") != key:
            continue
        for field in ("valueInt32", "valueString", "valueUint32", "valueInt64", "valueFloat"):
            values = item.get(field)
            if isinstance(values, list) and values:
                return values[0]
            if values not in (None, []):
                return values
    return None


def detail_list(annotation, key):
    for item in annotation.get("details", []):
        if not isinstance(item, dict) or item.get("key") != key:
            continue
        for field in ("valueInt32", "valueUint32", "valueInt64", "valueString"):
            values = item.get(field)
            if isinstance(values, list):
                return list(values)
    return []


def match_mode_from_win_condition(value):
    """BO1/BO3 from gameInfo.matchWinCondition, the only authoritative source.

    An unrecognised condition returns None so the caller records `unknown` instead of
    guessing the mode from the event name or from the sideboard size.
    """
    if not isinstance(value, str):
        return None
    normalized = value.replace(" ", "_").replace("__", "_")
    return MATCH_WIN_CONDITIONS.get(value) or MATCH_WIN_CONDITIONS.get(normalized)
