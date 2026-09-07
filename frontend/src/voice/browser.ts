import type { SpeechPort, TranscriptPort } from "./session";
interface Recognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
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
  SpeechRecognition?: new () => Recognition;
  webkitSpeechRecognition?: new () => Recognition;
};
export function browserTranscription(): TranscriptPort | null {
  const scope = window as SpeechWindow;
  const Constructor = scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
  if (!Constructor) return null;
  let current: Recognition | null = null;
  return {
    start(handlers) {
      current?.abort();
      const recognition = new Constructor();
      current = recognition;
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = document.documentElement.lang || "en-US";
      recognition.onresult = (event) => {
        const results = Array.from(event.results);
        handlers.result(
          results.map((r) => r[0].transcript).join(" "),
          results.every((r) => r.isFinal),
        );
      };
      recognition.onerror = (event) =>
        handlers.error(
          event.error === "not-allowed" || event.error === "service-not-allowed"
            ? "Microphone permission denied. You can retry or type your message."
            : "Transcription interrupted. Review the partial transcript or use text.",
        );
      recognition.onend = handlers.end;
      recognition.start();
    },
    stop() {
      current?.stop();
    },
    abort() {
      if (current) {
        current.onresult = null;
        current.onerror = null;
        current.onend = null;
        current.abort();
        current = null;
      }
    },
  };
}
export function browserSpeech(): SpeechPort | null {
  if (!("speechSynthesis" in window)) return null;
  let owned = false;
  return {
    speak(text, ended, failed) {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.onend = () => {
        owned = false;
        ended();
      };
      utterance.onerror = () => {
        owned = false;
        failed();
      };
      owned = true;
      window.speechSynthesis.speak(utterance);
    },
    cancel() {
      if (owned) {
        owned = false;
        window.speechSynthesis.cancel();
      }
    },
  };
}
