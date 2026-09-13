# Propaganda/TL media context

Wayfarer implements the Propaganda skill from Basic Set Characters B216 as an
ordinary social success roll whose media context comes only from pinned campaign
data. B216 identifies the skill as `/TL`, applies it to groups rather than
individuals, and leaves the necessary time and exposure to the GM. It does not
provide a universal table mapping TL to reach or duration, so the engine does not
invent one.

`PropagandaRules` records each campaign medium's TL range, maximum audience,
attempt time, persistence, prerequisites, and capability. A trigger selects the
medium by ID. The social service then derives the campaign TL from
`CharacterCompiler.policy.technology_level`; neither a scenario trigger nor an
LLM can supply the number. Missing policy, missing TL, an unknown or out-of-range
medium, an unregistered capability, and missing approved skill/equipment
prerequisites all reject before dice.

The existing social receipt stores the B216 roll trace privately and projects
the named outcome together with the bounded media facts. A zero persistence
marks a scene-bound result; otherwise `ends_at` is measured from completion of
the authored attempt. The attempt advances the shared resource clock through the
existing `Advance` reducer, so hazard and recovery deadlines retain their normal
guards. Receipt replay neither rerolls nor advances time again.

The skill enters the rules catalog only in Basic Set profile revision 11 and
package `0.9.0`. Revisions 8 through 10 retain their original package definition,
where Propaganda is absent. Revision 11 pins an explicit TL 8 campaign policy;
other campaign policies must likewise declare a TL and migrate explicitly.

Source coverage: Basic Set Characters, third printing, B216 and the B301-B304
skill index. Expected roll and media-result values are independently recorded in
`tests/fixtures/gurps/social_skills.json` and `tests/test_propaganda_media.py`.
