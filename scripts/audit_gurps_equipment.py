"""Emit the item-level GURPS equipment audit; --require-complete gates release evidence."""

import argparse
import json
from pathlib import Path

from wayfarer.simulation.equipment_audit import audit_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = audit_report(Path(__file__).resolve().parents[1])
    print(json.dumps(result, indent=2))
    if args.require_complete and not result["audit_complete"]:
        raise SystemExit("Equipment table audit incomplete; see named blockers above")
