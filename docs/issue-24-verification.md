# Issue 24 — voice/text parity verification

Voice is a browser-local adapter over the ordinary reviewed text command path.
There is deliberately no second voice command endpoint: recognition results remain
ephemeral until the player edits and submits them, at which point the normal HTTP
action receipt supplies command identity, authorization, version checks and retry
recovery. Browser narration reads already-visible completed narration and cannot
commit, cancel or reverse an engine action.

## Supported behavior

- Recognition is enabled only on a secure context when the browser exposes the
  standard `SpeechRecognition` constructor or Safari's prefixed
  `webkitSpeechRecognition` constructor. Chromium desktop/Android and Safari are
  supported when that API is present. Firefox and policy-disabled browsers use
  the complete text path. Runtime feature detection, not the user-agent string,
  is authoritative.
- Microphone permission is requested only after the first-use disclosure.
  Permission denial, missing input, network failure, no speech, timeout and partial
  results preserve a recoverable review or return to text without submission.
- Listening, interpreting, resolving and narrating are visible states. Starting
  capture interrupts only this page's playback. Scope changes, revocation,
  disconnect and unload fence late recognition callbacks and erase private buffers.
- A reviewed transcript is consumed synchronously and enters the same `PlayStore`
  path as typed text. Lost acknowledgements retry the original request and command
  ID; repeated review clicks cannot create a second command.

## Executable evidence

`frontend/src/voice/speech.test.ts` certifies both browser constructor adapters,
secure-context fallback and callback detachment. `frontend/src/voice/controller.test.ts`
certifies review, partial/error recovery, duplicate prevention, original-request
retry, scope fencing and player-local interruption. `frontend/tests/voice.spec.ts`
exercises the rendered states and browser fallbacks. The live two-identity journey
in `frontend/live-tests/setup-voice.spec.ts` proves that reviewed speech produces
exactly one action through the real Python director while the peer receives no
private transcript.

`scripts/check_browser_evidence.py` names the required voice journeys explicitly,
so a green report containing only unrelated browser tests cannot satisfy the
product gate. Actual microphone hardware and vendor transcription accuracy remain
environmental behavior; they do not weaken the fail-closed text fallback.
