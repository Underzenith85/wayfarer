"""Publish declared mechanics evidence; missing, failed or skipped evidence blocks release."""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from wayfarer.rules.catalog import PROTOTYPE_PACKAGE

ROOT = Path(__file__).resolve().parents[1]
# All cases in each required module must pass, including every parametrized backend/route.
MECHANICS: dict[str, tuple[str, ...]] = {
    "Pinned sources and rules": ("test_rules",),
    "GURPS profile checks, contests and resistance": (
        "test_gurps_conformance",
        "test_gurps_checks",
    ),
    "Legality, effects and power approval": ("test_compiler", "test_power", "test_wave5"),
    "Resources, clocks and conservation": ("test_resources", "test_release_invariants"),
    "Deterministic actions and adjudication": ("test_actions", "test_adjudication"),
    "Combat and injury": ("test_combat", "test_wave9"),
    "Replay, concurrency and crash recovery": ("test_postgres", "test_release_invariants"),
    "Advancement and explicit migration": ("test_advancement", "test_profiles"),
    "Authorization and knowledge isolation": ("test_campaign_api", "test_v1_api", "test_wave9"),
    "Provider attacks, degradation and narration authority": (
        "test_codex_provider",
        "test_llm",
        "test_wave9",
        "test_release_invariants",
    ),
    "Scenario creation, legal activation and director": ("test_wave11", "test_wave12"),
    "Epilogue, rewards and continuation": ("test_wave13",),
    "Reference adventure, all endings, split/capture/rescue and generated contracts": (
        "test_wave14",
    ),
}


def evaluate(report: Path) -> tuple[list[dict[str, object]], list[str]]:
    cases = list(ET.parse(report).getroot().iter("testcase"))
    errors: list[str] = []
    if not cases:
        errors.append("Test report contains no cases")
    for case in cases:
        optional_smoke = (
            case.get("classname", "").split(".")[-1] == "test_codex_provider"
            and case.get("name") == "test_authenticated_codex_smoke"
            and case.find("skipped") is not None
        )
        if not optional_smoke and any(
            case.find(tag) is not None for tag in ("failure", "error", "skipped")
        ):
            errors.append(f"Non-passing evidence: {case.get('classname')}.{case.get('name')}")
    required: list[str] = json.loads((ROOT / "tests/fixtures/release_cases.json").read_text())
    observed = {
        c.get("classname", "").replace(".", "/") + ".py::" + c.get("name", "") for c in cases
    }
    errors.extend(f"Missing required case: {name}" for name in required if name not in observed)
    rows: list[dict[str, object]] = []
    for mechanic, modules in MECHANICS.items():
        selected = [c for c in cases if c.get("classname", "").split(".")[-1] in modules]
        missing = [
            m
            for m in modules
            if not any(c.get("classname", "").split(".")[-1] == m for c in selected)
        ]
        errors.extend(f"Missing evidence: {mechanic}: {m}" for m in missing)
        rows.append(
            {
                "mechanic": mechanic,
                "modules": modules,
                "cases": len(selected),
                "status": "missing" if missing else "reported",
            }
        )
    return rows, errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/release"))
    parser.add_argument("--product", action="store_true")
    args = parser.parse_args()
    rows, errors = evaluate(args.report)
    approved = json.loads((ROOT / "tests/fixtures/approved_rules.json").read_text())
    current = {
        "package": json.loads(PROTOTYPE_PACKAGE.canonical_json()),
        "digest": PROTOTYPE_PACKAGE.digest,
    }
    if approved != current:
        errors.append("Approved-source fixture differs from declared prototype rules")
    product = json.loads((ROOT / "docs/product-release.json").read_text())
    if args.product:
        errors.extend(
            f"Product prerequisite pending: #{item['issue']} {item['reason']}"
            for item in product["pending"]
        )
    args.output.mkdir(parents=True, exist_ok=True)
    result = {
        "revision": os.environ.get("GITHUB_SHA", "local"),
        "scope": "product" if args.product else "engine",
        "passed": not errors,
        "mechanics": rows,
        "errors": errors,
        "approved_rules": approved,
        "product_prerequisites": product,
    }
    (args.output / "mechanics.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Release evidence",
        "",
        f"Result: {'BLOCKED' if errors else 'PASS'}",
        "",
        "Evidence covers original Wayfarer prototype mechanics, not full GURPS compliance.",
        "",
        "| Mechanic | Test cases |",
        "| --- | --- |",
    ]
    lines.extend(f"| {r['mechanic']} | {r['cases']} |" for r in rows)
    lines.extend(
        [
            "",
            "## Declared rule definitions",
            "",
            "| Definition | Status | Source |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(f"| {d.id} | {d.status} | {d.source_id} |" for d in PROTOTYPE_PACKAGE.definitions)
    lines.extend(
        [
            "",
            "Unsupported: full published GURPS catalog, magic/psionics, vehicles, "
            "and mechanics without an approved implemented definition.",
            "",
        ]
    )
    lines.extend(f"- {e}" for e in errors)
    (args.output / "mechanics.md").write_text("\n".join(lines) + "\n")
    if errors:
        raise SystemExit("Release blocked:\n" + "\n".join(errors))
    print("Required engine evidence passed; mechanics report published.")


if __name__ == "__main__":
    main()
