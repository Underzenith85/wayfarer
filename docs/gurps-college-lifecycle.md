# Shared spell lifecycle evidence

Issue #747 uses the same scheduled cast/effect records, resource settlement and
retry ledger as ordinary concrete spells. Characters third printing B236–241
and B249–251 were reopened alongside Campaigns fourth printing B348–349.

A resisted spell reuses its already drawn casting roll as the attacker roll in
the shared Quick Contest procedure. The Rule of 16 caps effective attacking skill
at the higher of 16 and the subject's resistance; ties protect the subject.
Casting failure does not draw resistance and critical casting success bypasses
resistance. No extra attack roll or alternate randomness source is introduced.

`tests/test_college_lifecycle.py` observes actual Light illumination at expiry,
atomic failed maintenance, paid extension, JSON restart, exact retry, altered
retry rejection, paid cancellation, and concentration eligibility after
interruption. It also observes the terminal/active resulting cast state and dice
consumption for resisted casts. Existing source spell integration tests preserve
concentration loss and Daze eligibility behavior.

Maintenance and termination settle in one immutable transition and one existing
transaction; a rejected payment cannot charge pools, apply effects or append a
receipt. Expiry removes effects from their actual consumers at the shared clock
boundary. The shared lifecycle blockers can be removed while individual effect
blockers remain. The source/evidence inventory therefore stays partial for all
96 currently unsupported effects. This does not certify all spells or the
end-to-end API/UI/LLM gates.
