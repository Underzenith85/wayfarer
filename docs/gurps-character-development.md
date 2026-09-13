# GURPS character development (#499)

The engine implements the selected Basic Set procedures on B290-B294 through
`simulation.campaign.development`. Character development is authored campaign
data and deterministic settlement; it does not introduce another character
compiler or bypass the existing build revision, approval, receipt, or replay
boundaries.

Adventure awards name the abilities used in play. Earned ledger entries retain
that eligibility and the source occurrence. Traits gained in play become typed
permissions that still require the ordinary legal build and power approval
before they can appear on the character.

Study consumes an existing Time Use entry exactly once. One point requires 200
learning hours. Self-study converts two hours to one learning hour, professional
education converts one-for-one, intensive training converts one-to-two, and job
or adventure credit uses the conversion already authored by Time Use. Partial
hours remain in `StudyProgress`; daily limits cap self-study, instruction,
intensive training, and on-the-job learning. A professional teacher needs
Teaching 12+ and must know the subject at least as well as the student; intensive
training also requires strictly greater subject level and points.

Quick learning records the stressful default attempt in authored rules, requires
an earned point from the previous session, resolves the B292 IQ roll with the
Eidetic/Photographic Memory bonus, and produces a one-skill permission only on
success. Skills without a default reject explicitly.

Learnable advantages require both an authored access path and explicit campaign
permission. Their completed study produces source-limited points and an approval
permission, never an automatic trait mutation.

`tests/test_character_development.py` contains independent expected results for
200-hour settlement, interrupted study, invalid teachers, exact-once retry,
adventure eligibility, gained-in-play approval, and quick learning.
