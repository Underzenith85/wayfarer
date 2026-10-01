# Authoritative college spell dispatch

Issue #746 routes executable college spells through `dispatch_college_spell` and
the existing source-reviewed execution-v2 reducer. Typed `SpellCommand` operations
use approved campaign channels and builds; the caller cannot supply success,
skill, resistance, casting time, or an effect flag. Light, Daze, Create Fire and
Fireball retain their concrete reducers, scheduled cast records, energy rules,
critical-failure handling, and resulting effect consumers.

The previous `apply_college_spell` check-only path cannot execute effects and now
rejects before drawing dice, adding receipts, or charging resources. It retains
readable historical receipt data without manufacturing new semantic successes.
Authority is checked before replay. Unsupported spells cannot enter the typed
execution dispatch. Unreviewed execution variants remain rejected.

Characters third printing B235–241 was reopened for the shared payment and time
rules. `tests/test_college_dispatch.py` verifies actual FP changes, casting time,
consecutive concentration, Daze eligibility, Light illumination, insufficient
energy rejection, exact retries, stale revisions, and authority. The college
inventory tests verify atomic rejection of unsupported check-only attempts.

The 96 college inventory spells without concrete execution retain their identity
and source locators as partial evidence with explicit effect-group blockers.
Historic concrete Light/Daze/Create Fire/Fireball evidence remains intact.
Construction, runtime dispatch, lifecycle, individual effects, and end-to-end
API/UI/LLM compliance remain distinct gates; this change does not certify the
whole spell family. Shared lifecycle is #747 and individual effects are #772–805.
