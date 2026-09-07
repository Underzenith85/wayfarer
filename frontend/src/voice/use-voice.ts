import { useEffect, useState, useSyncExternalStore } from "react";
import { usePlay } from "../play/use-play";
import { browserSpeech } from "./speech";
import { VoiceController } from "./controller";

/**
 * One controller per play workspace. Speech input now lives in the composer and
 * narration playback beside the transcript (#195), and both read the same audio
 * lifetime: starting a capture still interrupts local narration, and a scope
 * change still clears both. Two controllers would speak the same narration
 * twice and could not interrupt each other.
 */
export function useVoice() {
  const { store } = usePlay();
  const [voice] = useState(
    () => new VoiceController(store, browserSpeech(), "action"),
  );
  useEffect(() => voice.connect(), [voice]);
  useEffect(() => {
    const suspend = () => voice.suspend();
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
  return voice;
}

export function useVoiceState(voice: VoiceController) {
  return useSyncExternalStore(voice.subscribe, voice.getSnapshot);
}
