# High-speed Wait source review

Source baseline: Basic Set Campaigns fourth printing, PDF SHA256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
B366 and B385 allow a declared Wait to interrupt the triggering action; B394
constrains the entering turn and later high-speed trajectory. A non-triggering
Wait therefore cannot prohibit otherwise legal high-speed movement.

The complete proposed path is validated before a Wait can pause it. A matched
movement checkpoint persists its direction, accumulated straight distance, entry
turn count and remaining distance. Resumption spends only that remaining distance;
multiple declared waiters may react at the same checkpoint in existing encounter
order. The entering turn's one direction change remains shared across segments.
A reaction that removes the actor's ability to act retires the unused command.
This does not simulate falling, skids, collision or momentum after incapacitation.

Cancelling the unused high-speed path is rejected because an immediate stop would
bypass the separately unsupported B395 braking rules. Acceleration, deceleration,
difficult terrain and vehicle controls remain outside this bounded repair. It
contributes to #771 and does not certify every maneuver or the API/UI/LLM gates.

`tests/test_issue_771_high_speed_wait.py` covers entering and established motion,
triggering and non-triggering declarations, actual reaction and defense, original
command retry, restart, stale revision rejection, exact resume retry, two waiters
at one checkpoint, and short/non-adjacent invalid paths without state mutation.
