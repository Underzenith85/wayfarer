/** Speech produces an editable draft only. The play store owns command identity. */
export interface TranscriptPort {
  start(handlers: {
    result: (text: string, final: boolean) => void;
    error: (message: string) => void;
    end: () => void;
  }): void;
  stop(): void;
  abort(): void;
}
export interface SpeechPort {
  speak(text: string, ended: () => void, failed: () => void): void;
  cancel(): void;
}
export interface VoiceState {
  phase: "idle" | "listening" | "review" | "narrating" | "error";
  text: string;
  partial: boolean;
  error: string;
}
export class VoiceSession {
  private generation = 0;
  private state: VoiceState = {
    phase: "idle",
    text: "",
    partial: false,
    error: "",
  };
  private listeners = new Set<() => void>();
  constructor(
    private input: TranscriptPort | null,
    private output: SpeechPort | null,
  ) {}
  get supported() {
    return this.input !== null;
  }
  get canSpeak() {
    return this.output !== null;
  }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private patch(value: Partial<VoiceState>) {
    this.state = { ...this.state, ...value };
    this.listeners.forEach((f) => f());
  }
  listen() {
    this.cancel();
    if (!this.input) {
      this.patch({
        phase: "error",
        error:
          "Microphone transcription is unavailable. Type your message instead.",
      });
      return;
    }
    const generation = this.generation;
    this.patch({ phase: "listening" });
    try {
      this.input.start({
        result: (text, final) => {
          if (generation === this.generation)
            this.patch({ text: text.slice(0, 2000), partial: !final });
        },
        error: (error) => {
          if (generation === this.generation) {
            this.generation++;
            this.input?.abort();
            this.patch({ phase: "error", error });
          }
        },
        end: () => {
          if (generation === this.generation) {
            this.generation++;
            this.patch({ phase: this.state.text ? "review" : "idle" });
          }
        },
      });
    } catch {
      this.cancel();
      this.patch({
        phase: "error",
        error: "Microphone could not start. Check permission or use text.",
      });
    }
  }
  stop() {
    if (this.state.phase !== "listening") return;
    // Stop may deliver a final result asynchronously. Freeze the visible partial
    // transcript for explicit review, and fence every callback from this capture.
    this.generation++;
    this.input?.stop();
    this.patch({ phase: this.state.text ? "review" : "idle" });
  }
  edit(text: string) {
    this.patch({ text: text.slice(0, 2000), phase: "review" });
  }
  take(): string {
    if (this.state.phase !== "review" && this.state.phase !== "error")
      return "";
    const text = this.state.text.trim();
    this.cancel();
    return text;
  }
  speak(text: string) {
    this.cancel();
    if (!this.output || !text.trim()) return;
    const generation = this.generation;
    this.patch({ phase: "narrating" });
    this.output.speak(
      text,
      () => {
        if (generation === this.generation) this.patch({ phase: "idle" });
      },
      () => {
        if (generation === this.generation)
          this.patch({
            phase: "error",
            error: "Audio unavailable. Narration remains readable.",
          });
      },
    );
  }
  cancel() {
    this.generation++;
    this.input?.abort();
    this.output?.cancel();
    this.patch({ phase: "idle", text: "", error: "", partial: false });
  }
}
