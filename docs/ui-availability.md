# Unavailable states in the UI (#160)

A condition that removes capabilities is stated **once**, in one wording, and
every control it blocks repeats only the part that applies to that control.

## Where the wording lives

`frontend/src/presentation/availability.ts` holds every sentence: the banner
summary and its disclosure list, the per-control reasons, the reason a campaign
gives for an action kind it does not advertise, and the one phrasing for a detail
the current connection does not project. No surface writes its own variant.

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

## Server side

Capability accuracy is what makes this work: see the capability policy in
[the API runtime notes](api-v1-runtime.md). A campaign advertises `actions.inspect`
only where the caller already sees a target carrying an authored inspection check,
names those targets individually, and advertises `actions.use_item` only where the
scenario defines a consumable.

## Tests

`frontend/src/play/availability.test.tsx` covers the blocked composer, the
withheld scene action and the banner's disclosure;
`frontend/src/play/store.test.ts` pins the reasons to the conditions;
`frontend/src/setup/lobby.test.tsx` checks the setup shell states it once;
`frontend/startup-tests/new-game.spec.ts` checks the same in the production
build against a server with no provider; `tests/test_v1_api.py` checks that
advertised capabilities name only what the engine executes.
