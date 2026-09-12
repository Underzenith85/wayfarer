"""Generate selected trait/background coverage from the actual candidate package."""

import json

from wayfarer.engine.rules.traits.mundane import audit_report

if __name__ == "__main__":
    print(json.dumps(audit_report(), indent=2))
