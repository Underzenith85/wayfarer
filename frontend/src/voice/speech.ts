export interface RecognitionEvents {
  started(): void;
  /** Complete current recognition result, not a delta to append. */
  transcript(text: string, partial: boolean): void;
  ended(): void;
  failed(code: string): void;
}
export interface Capture {
  stop(): void;
  abort(): void;
}
export interface SpeechPort {
  readonly recognitionAvailable: boolean;
  readonly narrationAvailable: boolean;
  listen(events: RecognitionEvents): Capture;
  speak(text: string, ended: () => void, failed: () => void): () => void;
}

// SpeechRecognition is not part of TypeScript's cross-browser DOM library.
export interface NativeRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: (() => void) | null;
  onresult:
    | ((event: {
        results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
      }) => void)
    | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => NativeRecognition;
  webkitSpeechRecognition?: new () => NativeRecognition;
};
/** Feature detection is not a browser-support guarantee; text always remains available. */
export function browserSpeech(): SpeechPort {
  const browser = window as SpeechWindow;
  const Recognition =
    browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
  const synthesis = window.speechSynthesis;
  return {
    recognitionAvailable: !!Recognition && window.isSecureContext,
    narrationAvailable:
      !!synthesis && typeof SpeechSynthesisUtterance !== "undefined",
    listen(events) {
      if (!Recognition || !window.isSecureContext)
        throw new Error("Speech recognition is unavailable. Use text instead.");
      const recognition = new Recognition();
      recognition.lang = document.documentElement.lang || "en-US";
      recognition.continuous = true;
      recognition.interimResults = true;
      const detach = () => {
        recognition.onstart = null;
        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;
      };
      recognition.onstart = () => events.started();
      recognition.onresult = (event) => {
        const results = Array.from(event.results);
        events.transcript(
          results
            .map((r) => r[0].transcript)
            .join(" ")
            .trim(),
          results.some((r) => !r.isFinal),
        );
      };
      recognition.onerror = (event) => events.failed(event.error);
      recognition.onend = () => {
        detach();
        events.ended();
      };
      try {
        recognition.start();
      } catch (error) {
        detach();
        try {
          recognition.abort();
        } catch {
          /* Never mask start failure. */
        }
        throw error;
      }
      return {
        stop: () => recognition.stop(),
        abort: () => {
          detach();
          recognition.abort();
        },
      };
    },
    speak(text, ended, failed) {
      if (!synthesis || typeof SpeechSynthesisUtterance === "undefined")
        throw new Error("Spoken narration is unavailable.");
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = document.documentElement.lang || "en-US";
      utterance.onend = ended;
      utterance.onerror = failed;
      try {
        synthesis.speak(utterance);
      } catch (error) {
        utterance.onend = null;
        utterance.onerror = null;
        synthesis.cancel();
        throw error;
      }
      return () => {
        utterance.onend = null;
        utterance.onerror = null;
        synthesis.cancel();
      };
    },
  };
}
