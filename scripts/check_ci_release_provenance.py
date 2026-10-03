"""Reject release reports that describe anything except the exact tested checkout."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from scripts.ci_shard_protocol import load, manifest, mapping


def check(root: Path, manifest_path: Path, reports: Path, expected_head: str) -> None:
    source, _ = manifest(manifest_path, root)
    if source["head"] != expected_head:
        raise ValueError("requested CI head differs from clean checkout/manifest")
    basic_set = load(reports / "gurps-basic-set.json")
    mechanics = load(reports / "mechanics.json")
    nested = mapping(mechanics.get("gurps_basic_set"))
    if any(
        value != source["head"]
        for value in (
            basic_set.get("repository_commit"),
            mechanics.get("revision"),
            nested.get("repository_commit"),
        )
    ):
        raise ValueError("release report provenance differs from tested head")
    if mechanics.get("passed") is not True or mechanics.get("errors") != []:
        raise ValueError("mechanics evidence did not pass")
    print(f"Release provenance verified for {source['head']} tree {source['tree']}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("reports", type=Path)
    args = parser.parse_args()
    check(Path.cwd(), args.manifest, args.reports, os.environ.get("WAYFARER_CI_HEAD_SHA", ""))


if __name__ == "__main__":
    main()
