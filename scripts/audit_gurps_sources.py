"""Check audit integrity; --require-complete additionally gates source certification."""

import argparse
import json
from pathlib import Path

from wayfarer.source_audit import report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = report(Path(__file__).resolve().parents[1])
    print(json.dumps(result, indent=2))
    if args.require_complete and not result["audit_complete"]:
        raise SystemExit("Frozen-source audit incomplete; see named blockers above")
