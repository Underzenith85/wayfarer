# Unavailable states in the UI (#160)

A condition that removes capabilities is stated **once**, in one wording, and
every control it blocks repeats only the part that applies to that control.

## Where the wording lives

`frontend/src/presentation/availability.ts` holds every sentence: the banner
summary and its disclosure list, the per-control reasons, and the reason a
campaign gives for an action kind it does not advertise. No surface writes its
own variant. A region that is empty or that failed to load is a different
condition with its own shapes — `EmptyRegion` and `UnavailableRegion` in
`frontend/src/components/region-state.tsx` — and this module does not restate
them.

## The shell banner

`ProviderBanner` renders the condition once per shell — the setup lobby when
`/setups/session` reports no provider, the play shell when the open campaign does
not advertise `actions.text`. It names the condition in one sentence and puts the
affected capabilities, plus what still works, behind a **What is affected?**
disclosure. It is standing text, not a live region: the condition holds for the
whole session and must not compete with the status messages around it.

## Controls

`PlayStore.sendBlockReason(kind)` returns the single reason an intent cannot be
sent, in the order the store itself checks; `canSend` is that reason being null,
so a control and its explanation can never disagree. The composer disables the
whole input group — voice, textarea, counter and send — rather than the submit
button alone, and shows that reason, so no draft is typed into a box that cannot
submit. Scene actions follow the campaign's advertised capabilities: a kind the
engine would reject is not rendered as a control at all, and its observations
stay readable as scene detail under the reason they cannot be acted on. A control
blocked only by a passing condition (another action pending, a reconnect) stays
rendered and disabled, with that condition named beneath it.

## Drafts

Unsent composer text is private to one principal, campaign, scene and character,
so nothing written for one scene reappears in another, and ending the session
clears it with the rest of that principal's private storage. A draft states what
it is and how old it is — "Draft saved on this device 3 days ago" — and
**Discard draft** throws it away in one interaction, here and on the device
(#201). Because the field is disabled whenever submission is unavailable, a
draft can only ever be text the composer was willing to accept.

## Transcript

An entry shows what was submitted, when, and only then how it went (#202). The
frozen v1 action carries no intent text, so what this device submitted is kept
beside the drafts, under the same scope and the same session-end clearing, and
looked up by action id when a transcript is read back. A turn taken on another
device, or before this browser's storage was cleared, says so rather than
borrowing a placeholder that would make every entry read alike.

## Server side

Capability accuracy is what makes this work: see the capability policy in
[the API runtime notes](api-v1-runtime.md). A campaign advertises `actions.inspect`
only where the caller already sees a target carrying an authored inspection check,
names those targets individually, and advertises `actions.use_item` only where the
scenario defines a consumable.

## Tests

`frontend/src/play/availability.test.tsx` covers the blocked composer, the
withheld scene action and the banner's disclosure;
`frontend/src/play/drafts.test.tsx` covers the draft's age and its discard, and
`frontend/src/play/transcript-recall.test.tsx` covers what a reloaded transcript
shows and what it admits it does not have;
`frontend/src/play/store.test.ts` pins the reasons to the conditions;
`frontend/src/setup/lobby.test.tsx` checks the setup shell states it once;
`frontend/startup-tests/new-game.spec.ts` checks the same in the production
build against a server with no provider; `tests/test_v1_api.py` checks that
advertised capabilities name only what the engine executes.
