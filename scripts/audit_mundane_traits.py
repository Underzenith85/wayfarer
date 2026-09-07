"""Generate selected trait/background coverage from the actual candidate package."""

import json

from wayfarer.rules.mundane_traits import audit_report

if __name__ == "__main__":
    print(json.dumps(audit_report(), indent=2))
