# GURPS electronics

Issue #523 implements the communicator, sensor, and computer procedures from
Campaigns B471-472 and representative equipment facts from Characters B288-289.
The selected sources are the Campaigns fourth printing (April 2008, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`) and
Characters third printing (February 2008, SHA-256
`872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`).

Electronics profiles keep TL, media, range, operator skill, sensor mode,
Computer Complexity, storage, terminal, and operating time explicit. Stock
radios, phones, computers, a metal detector, and night-vision goggles bind those
profiles. Scenario-authored computer storage is independent of Complexity;
Complexity controls program eligibility and concurrent program capacity.

The electronics reducer is engine-authorized and receipt-idempotent. Power and
the required operating procedure are checked before dice. Communication range
extension uses the B471 operator penalty up to the stated maximum; unavailable
links record no information and consume no power. A sender can transmit only
facts already in its perspective. Successful recipients and authored
interceptors learn only the selected facts, while secure profiles suppress the
interceptor projection.

Sensors use the operator skill for a new sense and the supplied Perception level
for an augmented sense. Active-sensor distance penalties are applied per B472,
and a success reveals only the target facts in the trusted detection context.
Computers return task support: a program can supply an equipment bonus or the
TL at which a required technical program operates, but never resolves the task
itself. Capacity, storage, terminal, and skill failures reject explicitly.

`tests/test_electronics.py` covers exact catalog facts, pre-randomness power and
procedure rejection, scoped communication and interception, detection without
hidden-target leakage, program capacity, charge conservation, JSON-backed event
receipts, and retry without reroll or repeated consumption. Unsupported device
families and setting-specific networks remain authored catalog inputs rather
than inferred global services.
