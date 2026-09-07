import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Mic, Volume2 } from "lucide-react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import type { Channel } from "../play/transport";
import { browserSpeech } from "./speech";
import { VoiceController } from "./controller";

export function VoicePanel({ channel }: { channel: Channel }) {
  const { store, state: play } = usePlay();
  const [voice] = useState(
    () => new VoiceController(store, browserSpeech(), channel),
  );
  const state = useSyncExternalStore(voice.subscribe, voice.getSnapshot);
  const holding = useRef(false);
  useEffect(() => voice.connect(), [voice]);
  useEffect(() => {
    const suspend = () => {
      holding.current = false;
      voice.suspend();
    };
    const hidden = () => {
      if (document.hidden) suspend();
    };
    window.addEventListener("pagehide", suspend);
    window.addEventListener("blur", suspend);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("pagehide", suspend);
      window.removeEventListener("blur", suspend);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [voice]);
  const listening =
    state.capture === "requesting" || state.capture === "listening";
  const maximum = channel === "dialogue" ? 1975 : 2000;
  const status =
    state.capture === "requesting"
      ? "Requesting microphone permission…"
      : state.capture === "listening"
        ? "Listening — release to review"
        : state.capture === "interpreting"
          ? "Interpreting speech…"
          : state.capture === "review"
            ? "Review transcript — nothing has been sent"
            : state.speaking
              ? "Narrating — action already committed"
              : state.stage === "interpreting"
                ? "Interpreting action…"
                : state.stage === "resolving"
                  ? "Resolving action…"
                  : state.stage === "narrating"
                    ? "Preparing narration — action already committed"
                    : "Voice idle";
  const stopHold = () => {
    if (holding.current) {
      holding.current = false;
      voice.stop();
    }
  };
  return (
    <section className="voice-panel" aria-label="Voice controls">
      <h3>
        <Mic size={18} aria-hidden="true" /> Voice & narration
      </h3>
      <p id="voice-privacy">
        Optional browser speech. Your browser may send audio or narration text
        to its speech service. No microphone starts until you choose to talk.
        Text works without voice.
      </p>
      <p role="status" aria-live="polite">
        {status}
      </p>
      {!voice.speech.recognitionAvailable && (
        <p>
          Microphone speech recognition is unsupported or requires a secure
          browser context. Use the text composer below.
        </p>
      )}
      {state.error && <p role="alert">{state.error}</p>}
      <div className="context-actions">
        <Button
          type="button"
          className="push-to-talk"
          aria-describedby="voice-privacy"
          aria-label="Hold to talk"
          aria-pressed={listening}
          disabled={
            !voice.canListen() ||
            !["idle", "requesting", "listening"].includes(state.capture)
          }
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            holding.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            voice.start();
          }}
          onPointerUp={stopHold}
          onPointerCancel={() => {
            holding.current = false;
            voice.discard();
          }}
          onLostPointerCapture={stopHold}
          onKeyDown={(e) => {
            if ([" ", "Enter"].includes(e.key)) {
              e.preventDefault();
              if (!e.repeat) {
                holding.current = true;
                voice.start();
              }
            }
          }}
          onKeyUp={(e) => {
            if ([" ", "Enter"].includes(e.key)) {
              e.preventDefault();
              stopHold();
            }
          }}
          onClick={(e) => {
            if (e.detail === 0) {
              if (listening) voice.stop();
              else voice.start();
            }
          }}
        >
          <Mic size={18} aria-hidden="true" /> Hold to talk
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={
            !voice.canListen() ||
            !["idle", "requesting", "listening"].includes(state.capture)
          }
          onClick={() => (listening ? voice.stop() : voice.start())}
        >
          {listening ? "Stop listening" : "Start listening"}
        </Button>
        {state.capture !== "idle" && (
          <Button
            type="button"
            variant="outline"
            onClick={() => voice.discard()}
          >
            Discard voice input
          </Button>
        )}
      </div>
      <p className="composer-hint">
        Hold with pointer or Space, then release. Or use Start/Stop listening.
        Review and send separately.
      </p>
      {["listening", "interpreting"].includes(state.capture) &&
        state.transcript && (
          <p className="voice-partial">
            {state.partial ? "Partial transcript" : "Transcript"}:{" "}
            {state.transcript}
          </p>
        )}
      {state.capture === "review" && (
        <div className="voice-review">
          <label htmlFor="voice-review">Review voice transcript</label>
          <textarea
            id="voice-review"
            rows={3}
            value={state.transcript}
            onChange={(e) => voice.edit(e.target.value)}
            aria-describedby="voice-review-help"
          />
          <p id="voice-review-help">
            {state.partial
              ? "Recognition is incomplete. Check every word before sending. "
              : "Edit any recognition errors before sending. "}
            {state.transcript.length} / {maximum} characters. Sending uses your
            selected {channel} channel and current character.
          </p>
          {state.transcript.trim().length > maximum && (
            <p role="alert">
              Transcript is too long. Shorten it before sending.
            </p>
          )}
          <Button
            type="button"
            disabled={
              !store.canSend(channel === "ooc" ? "question" : "text") ||
              !state.transcript.trim() ||
              state.transcript.trim().length > maximum
            }
            onClick={() => void voice.submit()}
          >
            Send reviewed {channel === "ooc" ? "question" : channel}
          </Button>
        </div>
      )}
      <div className="narration-controls">
        <label>
          <input
            type="checkbox"
            checked={state.narrationEnabled}
            disabled={!voice.speech.narrationAvailable}
            onChange={(e) => voice.enableNarration(e.target.checked)}
          />{" "}
          Speak new completed narration
        </label>
        {!voice.speech.narrationAvailable && (
          <p>
            Spoken narration is unsupported. Written narration remains
            available.
          </p>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={!voice.speech.narrationAvailable}
          aria-pressed={state.muted}
          onClick={() => voice.mute(!state.muted)}
        >
          <Volume2 size={18} aria-hidden="true" />{" "}
          {state.muted ? "Unmute narration" : "Mute narration"}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!state.speaking}
          onClick={() => voice.stopNarration()}
        >
          Interrupt narration
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={
            !state.narrationEnabled ||
            state.muted ||
            state.capture !== "idle" ||
            !play.entries.some(
              (e) =>
                e.action?.status === "succeeded" &&
                e.narration?.status === "complete",
            )
          }
          onClick={() => voice.replay()}
        >
          Replay latest narration
        </Button>
        <p className="composer-hint">
          Mute and interruption stop only audio on this device. They never undo
          actions or cancel another player’s work.
        </p>
      </div>
    </section>
  );
}
