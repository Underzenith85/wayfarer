"""Run viewport/batch suites with independent backends and retain all evidence.

The journeys intentionally reuse Alice/Bob fixture identities. Sharing a backend
between viewport suites pools their requests into the same production rate limit.
Workshop authoring gets its own batch; no production limit is raised or disabled.
"""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from itertools import product
from pathlib import Path


def main() -> int:
    destination = Path(os.environ.get("PLAYWRIGHT_JUNIT_OUTPUT_FILE", "reports/live.xml"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    combined = ET.Element("testsuites")
    status = 0
    for project, batch in product(("desktop", "phone"), ("regular", "workshop")):
        name = f"{project}-{batch}"
        report = destination.with_name(f"live-{name}.xml")
        report.unlink(missing_ok=True)
        result = subprocess.run(
            [
                "pnpm",
                "exec",
                "playwright",
                "test",
                "--config",
                "playwright.live.config.ts",
                f"--project={project}",
                "--reporter=list,junit",
                f"--output=test-results/live-{name}",
                *sys.argv[1:],
            ],
            env={
                **os.environ,
                "PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(report),
                "WAYFARER_LIVE_BATCH": batch,
            },
            check=False,
        )
        status = status or result.returncode
        if report.exists():
            combined.extend(ET.parse(report).getroot())
        else:
            status = status or 1
            suite = ET.SubElement(combined, "testsuite", name=name)
            case = ET.SubElement(suite, "testcase", name=f"{name} live suite report")
            ET.SubElement(case, "error", message="Live suite produced no report")
    ET.ElementTree(combined).write(destination, encoding="utf-8", xml_declaration=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
