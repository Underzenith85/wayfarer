"""Emit reproducible item-level GURPS skill coverage, never inferred completeness."""

import json

from wayfarer.engine.rules.mundane_skills import audit_report

if __name__ == "__main__":
    print(json.dumps(audit_report(), indent=2))
