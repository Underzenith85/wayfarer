"""Publish declared mechanics evidence; missing, failed or skipped evidence blocks release."""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from wayfarer.certification.basic_set_certification import (
    evaluate as basic_set_certification_report,
)
from wayfarer.certification.source_audit import report as source_audit_report
from wayfarer.engine.rules.catalog import PROTOTYPE_PACKAGE

ROOT = Path(__file__).resolve().parents[1]
# All cases in each required module must pass, including every parametrized backend/route.
MECHANICS: dict[str, tuple[str, ...]] = {
    "Retained event and command schema versions": ("test_upcasters",),
    "Pinned sources and rules": ("test_rules",),
    "GURPS profile checks, contests and resistance": (
        "test_gurps_conformance",
        "test_gurps_checks",
    ),
    "Legality, effects and power approval": ("test_compiler", "test_power", "test_wave5"),
    "Resources, clocks and conservation": ("test_resources", "test_release_invariants"),
    "Deterministic actions and adjudication": ("test_actions", "test_adjudication"),
    "Combat and injury": ("test_combat", "test_wave9"),
    "Event fold and deterministic command re-execution": ("test_replay", "test_release_invariants"),
    "Replay, concurrency and crash recovery": (
        "test_postgres",
        "test_release_invariants",
        "test_snapshot_cache",
    ),
    "Advancement and explicit migration": ("test_advancement", "test_profiles"),
    "Authorization and knowledge isolation": ("test_campaign_api", "test_v1_api", "test_wave9"),
    "Provider attacks, degradation and narration authority": (
        "test_codex_provider",
        "test_llm",
        "test_wave9",
        "test_release_invariants",
    ),
    "Scenario creation, legal activation and director": (
        "test_wave11",
        "test_wave12",
        "test_scenario_references",
    ),
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
    from wayfarer.errors import StorageError
    from wayfarer.persistence.events import COMMAND_UPCASTERS
    from wayfarer.persistence.upcasters import EVENT_UPCASTERS

    retained = json.loads((ROOT / "tests/fixtures/retained_schemas.json").read_text())
    for group, registry in [("events", EVENT_UPCASTERS), ("commands", COMMAND_UPCASTERS)]:
        for schema in retained[group]:
            try:
                registry.check(schema["kind"], schema["version"])
            except StorageError as exc:
                errors.append(str(exc))
        rows.append(
            {
                "retained_schemas": group,
                "versions": retained[group],
                "mechanic": f"Retained {group}",
                "cases": len(retained[group]),
            }
        )
    return rows, errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/release"))
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--product", action="store_true")
    scope.add_argument("--gurps-lite", action="store_true")
    parser.add_argument(
        "--schema-usage",
        type=Path,
        help="JSON inventory from store.schema_usage() for retirement validation",
    )
    parser.add_argument("--gurps-source-audit", action="store_true")
    parser.add_argument("--gurps-basic-set", action="store_true")
    args = parser.parse_args()
    if args.gurps_lite:
        from scripts.lite_certification import evaluate_lite

        result = evaluate_lite(args.report)
        result["revision"] = os.environ.get("GITHUB_SHA", "local")
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "lite-certification.json").write_text(json.dumps(result, indent=2) + "\n")
        if not result["passed"]:
            raise SystemExit(f"Lite certification blocked: {result['errors']}")
        print("Required Lite certification evidence passed; report published.")
        return
    rows, errors = evaluate(args.report)
    if args.schema_usage:
        from wayfarer import validation
        from wayfarer.errors import StorageError
        from wayfarer.persistence.upcasters import EVENT_UPCASTERS, check_retention

        usage = [validation.mapping(row) for row in json.loads(args.schema_usage.read_text())]
        try:
            check_retention(EVENT_UPCASTERS, usage)
        except StorageError as exc:
            errors.append(str(exc))
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
    if args.gurps_source_audit:
        audit = source_audit_report(ROOT)
        if not audit["audit_complete"]:
            errors.append(
                "Frozen GURPS source audit incomplete; see scripts/audit_gurps_sources.py"
            )
    basic_set = basic_set_certification_report(ROOT) if args.gurps_basic_set else None
    if basic_set is not None and not basic_set.certified:
        errors.append(
            f"GURPS Basic Set certification has {len(basic_set.blockers)} unresolved blocker(s)"
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
        "gurps_basic_set": basic_set.as_dict() if basic_set is not None else None,
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
    if basic_set is not None:
        lines.extend(
            [
                "",
                "## GURPS Basic Set certification",
                "",
                f"Profile: `{basic_set.profile_id}@{basic_set.profile_version}`",
                f"Digest: `{basic_set.profile_digest}`",
                f"Result: {'PASS' if basic_set.certified else 'BLOCKED'}",
                f"Blockers: {len(basic_set.blockers)}",
            ]
        )
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
