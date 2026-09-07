# Voice input and narration controls (#57)

The Play composer offers optional browser speech recognition and spoken narration.
This is #57's frontend/browser-mock delivery; live voice/provider parity and the
supported-browser declaration remain #24/#30 and #59 Stage B. No voice endpoints,
provider integration, or changes to the frozen v1 wire contract are introduced.
These controls replace Wave 12's initial voice UI while retaining its authenticated
director action path and updating its two-player live integration test.

## Using voice

1. Select your campaign, controlled character and Action, Dialogue or OOC channel.
2. Hold **Hold to talk** with a pointer or Space/Enter, then release. The separate
   **Start listening** / **Stop listening** toggle is an alternative to holding.
3. Review and edit the transcript. Interim results are explicitly marked partial;
   they are never sent automatically, including on errors or microphone release.
4. Choose **Send reviewed action/dialogue/question**. This uses the same typed
   action path, actor/scene routing, version checks, command ID and receipt retry
   as text. Repeated clicks cannot consume a review twice. An unrelated typed
   draft stays intact. Unknown acceptance is retried with **Retry same request**.

No microphone starts on page load. Permission denial, missing microphone,
unsupported recognition, recognition network loss, silence and timeouts leave
the text composer available. Recognition failures preserve partial text for
explicit review; use **Discard voice input** to clear it and start again.
Capture stops after 60 seconds, and final recognition waits at most five seconds
after release. Overlong transcripts must be shortened; they are not silently
truncated into a different action.

## Narration and privacy

Spoken narration is off by default. Enable **Speak new completed narration** to
read new, complete narration for the current authorized actor and scene. This
does not speak provisional provider output or replay historical entries on load.
**Replay latest narration** is explicit. **Mute narration** and **Interrupt
narration** stop only this page's speech playback; they do not undo committed
results, abort provider narration, cancel requests, or affect another player's
actions. Starting a new capture also interrupts local speech to avoid feedback.

The audio controller subscribes directly to the play store. Principal, campaign,
scene, actor, membership-version or visibility-epoch changes, revoked access,
disconnects and scope reloads invalidate capture callbacks, clear review buffers,
and stop local speech. Leaving Play, changing message channel, hiding the page,
losing window focus or unloading it also stops audio and clears unsubmitted voice
text. Late browser callbacks cannot restore old private speech. Unsaved voice
buffers are memory-only and never copied into device draft storage. Explicitly
submitted text becomes part of the normal action/transcript flow.

Browser speech recognition is feature-detected (`SpeechRecognition` or its
prefixed variant) and requires a secure browser context. This is not a browser
support certification. Recognition can use the browser vendor's remote service,
and narration voices may also be service-backed: do not assume offline or
on-device processing. The UI discloses this before microphone use. See the
[SpeechRecognition documentation](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
and [speech playback cancellation](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/cancel).

## Verification

- `pnpm test` covers review gating, replacement of partial hypotheses, double
  submission, receipt retries, permission/network failure, length limits,
  scope revocation and switching, bounded waits, opt-in narration and interruption.
- `pnpm exec playwright test tests/voice.spec.ts` exercises browser API mocks on
  the normal phone/tablet/desktop projects, including revoked multiplayer access.
  These tests replace browser speech APIs; they do not request real microphones,
  make speech-service calls or certify actual recognition accuracy.
- `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm build`.

No additional frontend dependencies are required.
