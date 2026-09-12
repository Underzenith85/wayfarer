"""Import the selected Basic Set PDF structure into durable audit ledgers.

This maintainer tool is intentionally outside the installed package.  Release
certification reads only the checked-in JSON and never reads a PDF.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict, cast

BASELINE = "gurps-4e-characters-3p-2008+campaigns-4p-2008"
PROFILE = "gurps-basic-set-4e-2004"
SOURCE_DIGESTS = {
    "characters-third": "872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e",
    "campaigns-fourth": "79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80",
}
BROKEN_CHARACTER_DESTINATIONS = {
    "How GURPS Works: IQ": 15,
    "How GURPS Works: ST": 19,
    "Shopping for the Big": 20,
    "Skills for Design": 190,
}
REFERENCE_WORDS = ("index", "glossary", "ludography", "record sheet", "control sheet")
SOURCE_TITLE_FIXES = {"360°Vision": "360° Vision"}
OWNER_CAPABILITIES = {
    495: "gurps.character.primary_attributes",
    496: "gurps.character.traits",
    497: "gurps.character.ability_modifiers",
    498: "gurps.character.traits",
    500: "gurps.supernatural.abilities",
    501: "gurps.check.success",
    504: "gurps.world.physical_feats",
    505: "gurps.combat.maneuvers",
    506: "gurps.combat.melee_attack",
    507: "gurps.tactical.hex_movement",
    508: "gurps.combat.grappling",
    509: "gurps.tactical.visibility",
    510: "gurps.injury.hit_locations",
    511: "gurps.combat.ranged_attack",
    512: "gurps.injury.damage_types",
    513: "gurps.injury.armor_divisors",
    514: "gurps.injury.hp_thresholds",
    515: "gurps.recovery.medical_treatment",
    516: "gurps.recovery.fatigue",
    517: "gurps.world.environmental_hazards",
    518: "gurps.world.environmental_hazards",
    519: "gurps.world.environmental_hazards",
    520: "gurps.social.fright",
    521: "gurps.world.physical_feats",
    522: "gurps.combat.melee_attack",
    523: "gurps.equipment.catalog",
    524: "gurps.equipment.catalog",
    525: "gurps.equipment.catalog",
    526: "gurps.magic.spellcasting",
    527: "gurps.equipment.catalog",
    528: "gurps.vehicles.combat",
}

LedgerRow = dict[str, object]


class PdfWord(TypedDict):
    """Word geometry returned by pdfplumber for the fields this importer uses."""

    text: str
    x0: float
    top: float


def slug(value: str) -> str:
    value = value.replace("°", "").replace("’", "'").replace("–", "-")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "untitled"


def clean_title(value: object) -> str:
    return str(value).rstrip("\0\r").strip()


def section_owner(source: str, page: int, title: str) -> int:
    lower = title.lower()
    if "optional rule" in lower:
        return 493
    if source == "characters-third":
        if 32 <= page <= 165:
            return 497 if 101 <= page <= 118 else 496
        if page <= 233:
            return 495
        if page <= 257:
            return 500
        if page <= 263 or 295 <= page <= 323:
            return 498
        if page <= 294:
            return 527
        return 505
    if page <= 360:
        return 501
    if page <= 382:
        return 506 if any(word in lower for word in ("attack", "defen", "damage")) else 505
    if page <= 391:
        return 507
    if page <= 416:
        if any(word in lower for word in ("close combat", "multi-hex")):
            return 508
        if any(word in lower for word in ("surprise", "initiative", "visibility", "speed")):
            return 509
        if any(word in lower for word in ("hit location", "subdu", "melee")):
            return 510
        if any(word in lower for word in ("cover", "ranged", "guided", "overpenetr")):
            return 511
        if any(word in lower for word in ("area", "scatter", "explosion")):
            return 512
        return 513
    if page <= 443:
        if "fright" in lower:
            return 520
        if any(word in lower for word in ("poison", "drug", "intox", "withdraw")):
            return 518
        if any(word in lower for word in ("disease", "infection", "aging")):
            return 519
        if any(word in lower for word in ("fatigue", "starvation", "dehydr", "sleep")):
            return 516
        if any(word in lower for word in ("recovery", "first aid", "surgery", "medical")):
            return 515
        if any(word in lower for word in ("acid", "fire", "radiation", "suffocat", "hazard")):
            return 517
        return 514
    if page <= 453:
        return 498
    if page <= 460:
        return 522 if "combat" in lower else 521
    if page <= 484:
        if any(word in lower for word in ("invent", "prototype", "production")):
            return 524
        if "gadget" in lower:
            return 525
        if any(word in lower for word in ("computer", "sensor", "communicator")):
            return 523
        return 527
    if page <= 503:
        if any(word in lower for word in ("law", "control rating", "legality")):
            return 502
        if any(word in lower for word in ("economic", "job", "hireling", "loyalty")):
            return 503
        return 501
    return 494 if page >= 522 else 504


def section_shape(depth: int, page: int, title: str) -> tuple[str, str, str]:
    lower = title.lower()
    if "optional rule" in lower:
        return "optional-rule", "optional-unresolved", "absent"
    if "example" in lower:
        return "example", "reference-only", "not-applicable"
    if any(word in lower for word in REFERENCE_WORDS):
        return "reference", "reference-only", "not-applicable"
    if page >= 522:
        return "setting", "setting-unresolved", "absent"
    if depth == 0:
        return "structural-section", "required", "absent"
    return "mechanic", "required", "absent"


def import_sections(characters: Path, campaigns: Path) -> list[LedgerRow]:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    rows: list[LedgerRow] = []
    used: Counter[str] = Counter()

    def add_outline(source: str, book: str, pdf: Path, page_offset: int) -> None:
        reader = PdfReader(pdf)

        def walk(items: list[object], depth: int = 0, parent: str | None = None) -> None:
            previous: str | None = None
            for item in items:
                if isinstance(item, list):
                    walk(item, depth + 1, previous or parent)
                    continue
                destination = cast(dict[str, object], item)
                title = clean_title(destination.get("/Title"))
                page_index = reader.get_destination_page_number(item)
                page = (
                    BROKEN_CHARACTER_DESTINATIONS[title]
                    if page_index is None
                    else page_index + page_offset
                )
                base = f"section:{book}:b{page:03d}:{slug(title)}"
                used[base] += 1
                identifier = base if used[base] == 1 else f"{base}:{used[base]}"
                row_kind, disposition, implementation = section_shape(depth, page, title)
                owner = section_owner(source, page, title)
                rows.append(
                    {
                        "id": identifier,
                        "row_kind": row_kind,
                        "source_id": source,
                        "title": title,
                        "printed_page": page,
                        "list_page": None,
                        "profile_membership": [PROFILE],
                        "disposition": disposition,
                        "implementation": implementation,
                        "source_review": "pending",
                        "evidence_paths": [],
                        "historical_owners": [191],
                        "completion_owner": owner,
                        "capability_id": OWNER_CAPABILITIES.get(owner),
                        "runtime_binding": None,
                        "parent_id": parent,
                        "classification": None,
                        "listed_value": None,
                    }
                )
                previous = identifier

        walk(cast(list[object], reader.outline))

    # pypdf pages are zero-based: B1 is Characters index 2 and B337 is Campaigns index 2.
    add_outline("characters-third", "characters", characters, -1)
    add_outline("campaigns-fourth", "campaigns", campaigns, 335)
    return rows


def _line_words(words: list[PdfWord], y: float, low: float, high: float) -> list[PdfWord]:
    return sorted(
        (word for word in words if low <= word["x0"] < high and abs(word["top"] - y) < 1),
        key=lambda word: word["x0"],
    )


def _runtime_candidates(root: Path) -> dict[tuple[str, str, int], list[tuple[str, list[str]]]]:
    candidates: dict[tuple[str, str, int], list[tuple[str, list[str]]]] = defaultdict(list)
    supernatural = json.loads(
        (root / "src/wayfarer/engine/rules/supernatural/inventory.json").read_text()
    )
    for entry in supernatural["entries"]:
        if entry["kind"] in {"advantage", "disadvantage"}:
            key = (slug(entry["name"]), entry["kind"], entry["page"])
            candidates[key].append((f"supernatural/{entry['id']}", entry.get("evidence", [])))

    module = ast.parse((root / "src/wayfarer/engine/rules/traits/mundane/__init__.py").read_text())
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_entry" or len(node.args) < 4:
            continue
        try:
            values = [ast.literal_eval(arg) for arg in node.args[:4]]
        except ValueError:
            continue
        except TypeError:
            continue
        if not all(isinstance(value, (str, int)) for value in values):
            continue
        identifier, name, points, page = values
        category = "advantage" if int(points) >= 0 else "disadvantage"
        for keyword in node.keywords:
            if keyword.arg == "category" and isinstance(keyword.value, ast.Constant):
                category = str(keyword.value.value)
        if category in {"advantage", "disadvantage", "perk", "quirk", "background"}:
            key = (slug(str(name)), "advantage" if int(points) >= 0 else "disadvantage", int(page))
            candidates[key].append((f"trait:{identifier}", []))
    return candidates


def import_traits(characters: Path, root: Path) -> list[LedgerRow]:
    import pdfplumber  # type: ignore[import-not-found]

    page_ranges = {297: (230, 715), 298: (65, 735), 299: (145, 715), 300: (65, 510)}
    extracted: list[tuple[int, str, str, str, str, int, str]] = []
    with pdfplumber.open(characters) as pdf:
        for list_page, (minimum_y, maximum_y) in page_ranges.items():
            words = cast(
                list[PdfWord],
                pdf.pages[list_page + 1].extract_words(use_text_flow=False),
            )
            attr_headers = [word for word in words if word["text"] == "M/P/Soc"][:2]
            for side, header in enumerate(attr_headers):
                attr_x = header["x0"]
                xsup_x = next(
                    word["x0"]
                    for word in words
                    if word["text"] == "X/Sup"
                    and abs(word["top"] - header["top"]) < 1
                    and word["x0"] > attr_x
                )
                cost_x = next(
                    word["x0"]
                    for word in words
                    if word["text"] == "Cost"
                    and abs(word["top"] - header["top"]) < 1
                    and word["x0"] > xsup_x
                )
                page_x = next(
                    word["x0"]
                    for word in words
                    if word["text"] == "Page"
                    and abs(word["top"] - header["top"]) < 1
                    and word["x0"] > cost_x
                )
                low, high = (35, 305) if side == 0 else (300, 580)
                row_ys: list[float] = []
                for word in words:
                    if (
                        page_x - 25 <= word["x0"] < high
                        and minimum_y <= word["top"] <= maximum_y
                        and re.fullmatch(r"\d+[,.]?", word["text"])
                        and not any(abs(word["top"] - y) < 1 for y in row_ys)
                    ):
                        row_ys.append(word["top"])
                for y in sorted(row_ys):
                    line = _line_words(words, y, low, high)
                    title = " ".join(word["text"] for word in line if word["x0"] < attr_x - 10)
                    title = SOURCE_TITLE_FIXES.get(title, title)
                    trait_class = " ".join(
                        word["text"] for word in line if attr_x - 10 <= word["x0"] < xsup_x
                    )
                    source_class = " ".join(
                        word["text"] for word in line if xsup_x - 8 <= word["x0"] < cost_x
                    )
                    value = " ".join(
                        word["text"] for word in line if cost_x - 8 <= word["x0"] < page_x - 25
                    )
                    references = " ".join(
                        word["text"] for word in line if word["x0"] >= page_x - 25
                    )
                    page_match = re.search(r"\d+", references)
                    if not title or page_match is None:
                        raise RuntimeError(f"Could not parse trait row on B{list_page} at {y}")
                    kind = "advantage" if list_page <= 298 else "disadvantage"
                    extracted.append(
                        (
                            list_page,
                            kind,
                            title,
                            trait_class,
                            source_class,
                            int(page_match.group()),
                            value,
                        )
                    )
    if len(extracted) != 480:
        raise RuntimeError(f"Expected 480 named trait items, got {len(extracted)}")

    candidates = _runtime_candidates(root)
    rows: list[LedgerRow] = []
    used: Counter[str] = Counter()
    for list_page, kind, title, trait_class, source_class, page, value in extracted:
        base = f"trait:{kind}:{slug(title)}"
        used[base] += 1
        identifier = base if used[base] == 1 else f"{base}:{used[base]}"
        matches = candidates.get((slug(title), kind, page), [])
        binding, evidence = matches[0] if len(matches) == 1 else (None, [])
        rows.append(
            {
                "id": identifier,
                "row_kind": "catalog-item",
                "source_id": "characters-third",
                "title": title,
                "printed_page": page,
                "list_page": list_page,
                "profile_membership": [PROFILE],
                "disposition": "required",
                "implementation": "partial" if binding else "absent",
                "source_review": "pending",
                "evidence_paths": evidence,
                "historical_owners": [113],
                "completion_owner": 496,
                "capability_id": "gurps.character.traits",
                "runtime_binding": binding,
                "parent_id": None,
                "classification": f"{kind}|{trait_class}|{source_class}",
                "listed_value": value,
            }
        )

    # The source packet counts these seven explicit list semantics separately.
    # They are rollups only and can never satisfy one of the 480 named items.
    rollups = (
        ("advantage", "Advantages", 297),
        ("disadvantage", "Disadvantages", 299),
        ("perk", "Perks", 297),
        ("quirk", "Quirks", 299),
        ("mental", "Mental classification", 297),
        ("physical", "Physical classification", 297),
        ("social", "Social classification", 297),
    )
    for key, title, page in rollups:
        rows.append(
            {
                "id": f"trait:rollup:{key}",
                "row_kind": "rollup",
                "source_id": "characters-third",
                "title": title,
                "printed_page": page,
                "list_page": page,
                "profile_membership": [PROFILE],
                "disposition": "reference-only",
                "implementation": "not-applicable",
                "source_review": "pending",
                "evidence_paths": [],
                "historical_owners": [113],
                "completion_owner": 496,
                "capability_id": "gurps.character.traits",
                "runtime_binding": None,
                "parent_id": None,
                "classification": key,
                "listed_value": None,
            }
        )
    return rows


def import_modifiers(characters: Path) -> list[LedgerRow]:
    import pdfplumber

    configs = ((300, 615, 730), (301, 45, 380))
    extracted: list[tuple[int, str, str, str, int]] = []
    with pdfplumber.open(characters) as pdf:
        for list_page, minimum_y, maximum_y in configs:
            words = cast(
                list[PdfWord],
                pdf.pages[list_page + 1].extract_words(use_text_flow=False),
            )
            candidate_headers = sorted(
                (
                    word
                    for word in words
                    if word["text"] == "Page" and minimum_y - 20 <= word["top"] <= maximum_y
                ),
                key=lambda word: word["x0"],
            )
            page_headers: list[PdfWord] = []
            for header in candidate_headers:
                if not any(abs(header["x0"] - seen["x0"]) < 2 for seen in page_headers):
                    page_headers.append(header)
            page_headers = page_headers[:2]
            for side, page_header in enumerate(page_headers):
                page_x = page_header["x0"]
                low, high = (35, 305) if side == 0 else (300, 580)
                type_x = page_x - 85
                value_x = page_x - 45
                row_ys: list[float] = []
                for word in words:
                    if (
                        page_x - 20 <= word["x0"] < high
                        and minimum_y <= word["top"] <= maximum_y
                        and re.fullmatch(r"\d+", word["text"])
                        and not any(abs(word["top"] - y) < 1 for y in row_ys)
                    ):
                        row_ys.append(word["top"])
                for y in sorted(row_ys):
                    line = _line_words(words, y, low, high)
                    title = " ".join(word["text"] for word in line if word["x0"] < type_x - 8)
                    modifier_type = " ".join(
                        word["text"] for word in line if type_x - 8 <= word["x0"] < value_x - 8
                    )
                    value = " ".join(
                        word["text"] for word in line if value_x - 8 <= word["x0"] < page_x - 20
                    )
                    reference = " ".join(word["text"] for word in line if word["x0"] >= page_x - 20)
                    page_match = re.search(r"\d+", reference)
                    if not title or page_match is None:
                        raise RuntimeError(f"Could not parse modifier row on B{list_page} at {y}")
                    category = (
                        "limitation"
                        if (list_page == 301 and (side == 1 or y >= 330))
                        else "enhancement"
                    )
                    if modifier_type == "G":
                        category = "gadget-limitation"
                    extracted.append((list_page, category, title, value, int(page_match.group())))
    if len(extracted) != 87:
        raise RuntimeError(f"Expected 87 modifier rows, got {len(extracted)}")

    used: Counter[str] = Counter()
    rows: list[LedgerRow] = []
    for list_page, category, title, value, page in extracted:
        base = f"modifier:{category}:{slug(title)}"
        used[base] += 1
        identifier = base if used[base] == 1 else f"{base}:{used[base]}"
        rows.append(
            {
                "id": identifier,
                "row_kind": "catalog-item",
                "source_id": "characters-third",
                "title": title,
                "printed_page": page,
                "list_page": list_page,
                "profile_membership": [PROFILE],
                "disposition": "required",
                "implementation": "absent",
                "source_review": "pending",
                "evidence_paths": [],
                "historical_owners": [100],
                "completion_owner": 497,
                "capability_id": "gurps.character.ability_modifiers",
                "runtime_binding": None,
                "parent_id": None,
                "classification": category,
                "listed_value": value,
            }
        )
    return rows


def write_ledger(output: Path, ledger_type: str, rows: list[LedgerRow]) -> None:
    payload = {
        "schema_version": 1,
        "ledger_type": ledger_type,
        "baseline_id": BASELINE,
        "source_sha256": SOURCE_DIGESTS,
        "rows": rows,
    }
    (output / f"{ledger_type}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--campaigns", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    output = args.root / "src/wayfarer/certification/basic_set_audit"
    output.mkdir(parents=True, exist_ok=True)
    ledgers = {
        "sections": import_sections(args.characters, args.campaigns),
        "traits": import_traits(args.characters, args.root),
        "modifiers": import_modifiers(args.characters),
    }
    expected = {"sections": 711, "traits": 487, "modifiers": 87}
    actual = {name: len(rows) for name, rows in ledgers.items()}
    if actual != expected:
        raise RuntimeError(f"Imported denominator differs from source packet: {actual}")
    for name, rows in ledgers.items():
        write_ledger(output, name, rows)


if __name__ == "__main__":
    main()
