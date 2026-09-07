"""Reject missing, empty or non-passing browser reports, including silently skipped cases."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def check(directory: Path) -> list[str]:
    errors: list[str] = []
    for name in ("browser", "live", "reference", "startup"):
        path = directory / f"{name}.xml"
        if not path.is_file():
            errors.append(f"Missing {name} browser report")
            continue
        cases = list(ET.parse(path).getroot().iter("testcase"))
        if not cases:
            errors.append(f"Empty {name} browser report")
        if name == "reference" and len(cases) < 2:
            errors.append("Reference report must include both adventure and rescue journeys")
        for case in cases:
            if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
                errors.append(f"Non-passing {name} browser evidence: {case.get('name')}")
    return errors


if __name__ == "__main__":
    failures = check(Path(sys.argv[1]))
    if failures:
        raise SystemExit("\n".join(failures))
