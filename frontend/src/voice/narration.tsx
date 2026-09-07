import { Volume2, VolumeX } from "lucide-react";
import { Button } from "../components/ui/button";
import { Sheet } from "../components/ui/sheet";
import { usePlay } from "../play/use-play";
import type { VoiceController } from "./controller";
import { useVoiceState } from "./use-voice";

/**
 * Narration is output, not composition: it belongs where narration appears
 * (#195). The trigger reports whether this device is speaking; everything that
 * only matters once narration is on sits behind it.
 */
export function NarrationControls({ voice }: { voice: VoiceController }) {
  const { state: play } = usePlay();
  const state = useVoiceState(voice);
  const on = state.narrationEnabled && !state.muted;
  const replayable = play.entries.some(
    (e) =>
      e.action?.status === "succeeded" && e.narration?.status === "complete",
  );
  return (
    <Sheet
      title="Narration"
      description="Spoken narration plays on this device only. It never undoes an action, changes a committed result, or cancels another player’s work."
      trigger={
        <Button variant="outline" className="narration-trigger">
          {on ? (
            <Volume2 size={18} aria-hidden="true" />
          ) : (
            <VolumeX size={18} aria-hidden="true" />
          )}
          <span>Narration</span>
        </Button>
      }
    >
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
        <div className="context-actions">
          <Button
            type="button"
            variant="outline"
            disabled={!voice.speech.narrationAvailable}
            aria-pressed={state.muted}
            onClick={() => voice.mute(!state.muted)}
          >
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
              !replayable
            }
            onClick={() => voice.replay()}
          >
            Replay latest narration
          </Button>
        </div>
        <p className="composer-hint">
          Mute and interruption stop only audio on this device. They never undo
          actions or cancel another player’s work.
        </p>
      </div>
    </Sheet>
  );
}
