import { useRef, useState } from "react";
import { Mic, Square, X } from "lucide-react";
import { Button } from "../components/ui/button";
import type { VoiceController, VoiceState } from "./controller";

/**
 * Disclosed before the microphone starts, not as permanent body copy above the
 * input (#195). The same paragraph is the notice on first use and, afterwards,
 * the mic button's description.
 */
const speechPrivacy =
  "Optional browser speech. Your browser may send audio or narration text to its speech service. No microphone starts until you choose to talk. Text works without voice.";
const acknowledgement = "wayfarer-voice-notice";
/** A press shorter than this is a tap: it latches listening on until pressed again. */
const tap = 400;

function acknowledged() {
  try {
    return localStorage.getItem(acknowledgement) === "seen";
  } catch {
    return false;
  }
}

/**
 * The composer's one voice control: press and hold to talk, or tap to latch
 * listening on. It occupies no vertical space when unused, and the transcript
 * it captures is reviewed in the composer's own field.
 */
export function VoiceMic({
  voice,
  state,
}: {
  voice: VoiceController;
  state: VoiceState;
}) {
  const [notice, setNotice] = useState(false);
  const pressed = useRef<number | null>(null);
  const listening =
    state.capture === "requesting" || state.capture === "listening";
  const busy = !["idle", "requesting", "listening"].includes(state.capture);
  const begin = () => {
    if (listening) {
      pressed.current = null;
      voice.stop();
      return;
    }
    if (!acknowledged()) {
      pressed.current = null;
      setNotice(true);
      return;
    }
    pressed.current = Date.now();
    voice.start();
  };
  const release = () => {
    const at = pressed.current;
    pressed.current = null;
    // A tap leaves the microphone on; only a deliberate hold stops on release.
    if (at !== null && Date.now() - at >= tap) voice.stop();
  };
  const accept = () => {
    try {
      localStorage.setItem(acknowledgement, "seen");
    } catch {
      /* Storage can be disabled; the notice then shows once per visit. */
    }
    setNotice(false);
    voice.start();
  };
  return (
    <>
      <Button
        type="button"
        variant={listening ? "default" : "outline"}
        className="voice-mic"
        aria-describedby="voice-privacy"
        aria-label={listening ? "Stop voice input" : "Start voice input"}
        aria-pressed={listening}
        title="Hold to talk, or tap to keep listening"
        disabled={busy || (!voice.canListen() && !listening)}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          begin();
          try {
            // Keeps a hold alive when the pointer slides off the button.
            e.currentTarget.setPointerCapture(e.pointerId);
          } catch {
            /* Pointer capture is best effort; pointerup still ends the hold. */
          }
        }}
        onPointerUp={release}
        onPointerCancel={() => {
          pressed.current = null;
          voice.discard();
        }}
        onLostPointerCapture={release}
        onKeyDown={(e) => {
          if ([" ", "Enter"].includes(e.key)) {
            e.preventDefault();
            if (!e.repeat) begin();
          }
        }}
        onKeyUp={(e) => {
          if ([" ", "Enter"].includes(e.key)) {
            e.preventDefault();
            release();
          }
        }}
      >
        {listening ? (
          <Square size={18} aria-hidden="true" />
        ) : (
          <Mic size={18} aria-hidden="true" />
        )}
      </Button>
      {state.capture !== "idle" && (
        <Button
          type="button"
          variant="outline"
          className="voice-discard"
          onClick={() => voice.discard()}
        >
          <X size={18} aria-hidden="true" />
          Discard voice input
        </Button>
      )}
      {notice ? (
        <div
          className="voice-notice"
          role="dialog"
          aria-label="About browser speech"
        >
          <p id="voice-privacy">{speechPrivacy}</p>
          <div className="context-actions">
            <Button type="button" autoFocus onClick={accept}>
              Start listening
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => setNotice(false)}
            >
              Not now
            </Button>
          </div>
        </div>
      ) : (
        <p id="voice-privacy" className="visually-hidden">
          {speechPrivacy}
        </p>
      )}
    </>
  );
}
