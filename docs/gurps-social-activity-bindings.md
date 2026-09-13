# Teaching and Leadership activity bindings

Issue #369 closes the B224 Teaching and B204 Leadership outcomes that cross the
social-procedure boundary. The social roll remains in
`wayfarer.engine.rules.skills.mundane.social`; advancement and subgroup state remain owned by
their campaign services.

## Teaching

`DevelopmentRules.teaching` maps a trusted social trigger to an instructed
`StudyRule`. A `teaching-taught` result creates a single-use `TeachingLesson`.
The student must still submit the later study-settlement command: that command
consumes the lesson and settled Time Use credit, validates Teaching 12+ and the
teacher's subject competence, and awards only source-bound study points. Failed
rolls create no lesson, and the teacher never spends the student's points or
chooses their advancement.

## Leadership

`PartyRules.leadership` names the NPC followers and authored group activity a
trusted trigger governs. The result records every affected follower, the actual
group size, and a party activity receipt. Only `leadership-followed` commits the
activity; hesitation and refusal are rejected receipts. A follower listed as a
player character is rejected before state changes, so a Leadership roll cannot
move a PC or select a PC action.

B204 states no numerical modifier based on the number of followers. The binding
therefore records `group_size_modifier=0` rather than inventing a penalty; group
size scales the set of NPCs governed by the result. Existing source-defined
Voice and situational modifiers continue to be derived by the social procedure.
