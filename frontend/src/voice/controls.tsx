import { useEffect, useState, useSyncExternalStore } from "react";
import { Button } from "../components/ui/button";
import { usePlay } from "../play/use-play";
import { browserSpeech, browserTranscription } from "./browser";
import { VoiceSession } from "./session";
export function VoiceControls({
  onReview,
  disabled,
  narration,
}: {
  onReview: (text: string) => void;
  disabled: boolean;
  narration: string;
}) {
  const { state } = usePlay();
  const [session] = useState(
    () => new VoiceSession(browserTranscription(), browserSpeech()),
  );
  const voice = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    const stop = () => session.cancel();
    const hidden = () => {
      if (document.hidden) stop();
    };
    window.addEventListener("offline", stop);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("offline", stop);
      document.removeEventListener("visibilitychange", hidden);
      session.cancel();
    };
  }, [session]);
  useEffect(() => {
    if (disabled || state.expired || state.connection !== "online")
      session.cancel();
  }, [disabled, state.expired, state.connection, session]);
  return (
    <div className="voice-controls" aria-label="Voice controls">
      <p>
        Voice uses your browser’s speech service. Review the transcript before
        sending.
      </p>
      <p role="status">
        {voice.phase === "listening"
          ? "Listening…"
          : voice.phase === "narrating"
            ? "Narrating…"
            : voice.phase === "review"
              ? "Review your transcript"
              : disabled
                ? "Interpreting / resolving your action…"
                : "Text and voice use the same action controls."}
      </p>
      {voice.error && <p role="alert">{voice.error}</p>}
      {!session.supported && (
        <p>
          Speech recognition is unavailable in this browser. Text input remains
          available.
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={
          disabled || !session.supported || state.connection !== "online"
        }
        onClick={() =>
          voice.phase === "listening" ? session.stop() : session.listen()
        }
      >
        {voice.phase === "listening" ? "Stop and review" : "Start microphone"}
      </Button>
      {voice.text && (
        <>
          <label>
            Transcript {voice.partial && "(partial — check before using)"}
            <textarea
              aria-label="Voice transcript"
              value={voice.text}
              disabled={voice.phase === "listening"}
              onChange={(e) => session.edit(e.target.value)}
            />
          </label>
          <Button
            type="button"
            disabled={disabled || voice.phase === "listening"}
            onClick={() => {
              const text = session.take();
              if (text) onReview(text);
            }}
          >
            Use reviewed transcript
          </Button>
        </>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={
          !narration || !session.canSpeak || state.connection !== "online"
        }
        onClick={() => session.speak(narration)}
      >
        Read latest narration
      </Button>
      <Button type="button" variant="outline" onClick={() => session.cancel()}>
        Stop audio / discard transcript
      </Button>
    </div>
  );
}
