# Voice input and narration controls (#57)

The Play composer offers optional browser speech recognition and spoken narration.
This is #57's frontend delivery and #24's reviewed browser adapter. Live
voice/provider parity uses the ordinary text action endpoint and receipt; no voice endpoints,
provider integration, or changes to the frozen v1 wire contract are introduced.
These controls replace Wave 12's initial voice UI while retaining its authenticated
director action path and updating its two-player live integration test. #195 then
folded speech input into the composer and moved narration playback beside the
transcript; the data model was already this shape, only the presentation was not.

## Using voice

Voice is an input method for the composer, not a parallel channel (#195). One
mic control sits in the composer toolbar beside the channel switch, and it takes
no vertical space when unused.

1. Select your campaign, controlled character and Action, Dialogue or OOC channel.
2. Press and hold the mic with a pointer or Space/Enter and release to end the
   capture, or tap it to keep listening until you tap it again. The first press
   discloses browser speech and starts nothing until you choose **Start
   listening**; that acknowledgement is remembered on this device.
3. The field swaps into a listening state — an animated level meter, a live
   transcript and a stop control — and the transcript stays in the composer's own
   field for review. Interim results are explicitly marked partial in the live
   region; they are never sent automatically, including on errors or release.
4. Edit the transcript in place and choose **Send reviewed action/dialogue/
   question**, the composer's one primary action. This uses the same typed action
   path, actor/scene routing, version checks, command ID and receipt retry as
   text. Repeated clicks cannot consume a review twice. An unrelated typed draft
   stays intact and returns to the field afterwards. Unknown acceptance is
   retried with **Retry same request**.

No microphone starts on page load. Permission denial, missing microphone,
unsupported recognition, recognition network loss, silence and timeouts leave
the text composer available. Recognition failures preserve partial text for
explicit review; use **Discard voice input** to clear it, restore the typed
draft and start again. Capture stops after 60 seconds, and final recognition
waits at most five seconds after release. Overlong transcripts must be shortened;
they are not silently truncated into a different action.

## Narration and privacy

Narration is playback of what the transcript already shows, so its controls live
beside the transcript rather than inside the input (#195): **Narration** in the
"At the table" heading opens them, and its icon reports whether this device is
speaking. Spoken narration is off by default. Enable **Speak new completed
narration** to read new, complete narration for the current authorized actor and
scene. This does not speak provisional provider output or replay historical
entries on load. **Replay latest narration** is explicit. **Mute narration** and
**Interrupt narration** stop only this page's speech playback; they do not undo
committed results, abort provider narration, cancel requests, or affect another
player's actions. Starting a new capture also interrupts local speech to avoid
feedback.

One `VoiceController` serves the whole play workspace, so the mic and the
narration controls share a single audio lifetime: a capture can interrupt
narration, and one narration is never spoken twice. It subscribes directly to
the play store. Principal, campaign, scene, actor, membership-version or
visibility-epoch changes, revoked access, disconnects and scope reloads
invalidate capture callbacks, clear review buffers, and stop local speech.
Leaving Play, changing message channel, hiding the page, losing window focus or
unloading it also clear unsubmitted voice text; a channel change no longer stops
narration, which is not part of composing a turn. Late browser callbacks cannot
restore old private speech. Unsaved voice buffers are memory-only: the field
shows the transcript instead of the draft while a review is open, and never
copies it into device draft storage. Explicitly submitted text becomes part of
the normal action/transcript flow.

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
  `src/play/composer.test.tsx` covers the composer's structure: one mic, one
  channel control, one primary action, and narration outside the input.
- `pnpm exec playwright test tests/voice.spec.ts` exercises browser API mocks on
  the normal phone/tablet/desktop projects, including first-use disclosure,
  hold-versus-tap capture and revoked multiplayer access.
  These tests replace browser speech APIs; they do not request real microphones,
  make speech-service calls or certify actual recognition accuracy.
- `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm build`.

No additional frontend dependencies are required.
