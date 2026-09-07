import type { PlayStore, PlayState, Entry } from "../play/store";
import type { Channel } from "../play/transport";
import type { Capture, SpeechPort } from "./speech";

export interface VoiceState {
  scope: string | null;
  capture: "idle" | "requesting" | "listening" | "interpreting" | "review";
  transcript: string;
  partial: boolean;
  error: string | null;
  narrationEnabled: boolean;
  muted: boolean;
  speaking: boolean;
  stage: "idle" | "interpreting" | "resolving" | "narrating";
}
const initial: VoiceState = {
  scope: null,
  capture: "idle",
  transcript: "",
  partial: false,
  error: null,
  narrationEnabled: false,
  muted: false,
  speaking: false,
  stage: "idle",
};
const recognitionErrors: Record<string, string> = {
  "not-allowed":
    "Microphone permission was denied. Allow microphone access in browser settings, then try again, or use text.",
  "service-not-allowed":
    "The browser blocked speech recognition. Use text or review browser permissions.",
  "audio-capture":
    "No microphone is available. Check your microphone and try again, or use text.",
  network:
    "Speech recognition lost its network connection. Review any partial transcript or use text, then try again.",
  "no-speech": "No speech was recognized. Try again or type your action.",
  aborted: "Listening stopped. Review any partial transcript or use text.",
};

/** Local audio lifetime is independent of command execution and provider narration. */
export class VoiceController {
  private state: VoiceState = { ...initial };
  private listeners = new Set<() => void>();
  private unsubscribe: (() => void) | null = null;
  private scope: string | null = null;
  private captureHandle: Capture | null = null;
  private cancelSpeech: (() => void) | null = null;
  private captureSerial = 0;
  private speechSerial = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private seen = new Set<string>();
  constructor(
    readonly play: PlayStore,
    readonly speech: SpeechPort,
    private current: Channel,
  ) {}
  get channel() {
    return this.current;
  }
  /**
   * The composer owns the channel; one controller serves every channel so that
   * narration playback, which no longer lives in the composer, is not restarted
   * by a change of input mode. Unsent speech never crosses a channel: the
   * transcript was reviewed for the channel it was captured in.
   */
  setChannel(channel: Channel) {
    if (channel === this.current) return;
    this.discard();
    this.current = channel;
    this.sync();
  }
  getSnapshot = () => this.state;
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private patch(value: Partial<VoiceState>) {
    if (
      Object.entries(value).every(
        ([key, v]) => this.state[key as keyof VoiceState] === v,
      )
    )
      return;
    this.state = { ...this.state, ...value };
    this.listeners.forEach((fn) => fn());
  }
  connect() {
    this.sync();
    this.unsubscribe = this.play.subscribe(() => this.sync());
    return () => this.dispose();
  }
  private authorizedScope(s: PlayState) {
    const snapshot = s.snapshot,
      actor = s.actorId;
    if (
      !snapshot ||
      !actor ||
      s.expired ||
      s.loading ||
      s.needsRefresh ||
      s.connection !== "online" ||
      !snapshot.campaign.membership.actor_ids.includes(actor) ||
      !snapshot.scene.visible_actor_ids.includes(actor)
    )
      return null;
    return JSON.stringify([
      this.play.transport.principalId,
      snapshot.campaign.id,
      snapshot.scene.id,
      actor,
      snapshot.campaign.membership.version,
      s.multiplayer?.checkpoint.epoch ?? "",
    ]);
  }
  private eligible(entry: Entry) {
    const s = this.play.getSnapshot();
    return (
      entry.action?.status === "succeeded" &&
      entry.action.actor_id === s.actorId &&
      entry.action.scene_id === s.snapshot?.scene.id &&
      entry.narration?.status === "complete" &&
      !!entry.narration.text.trim()
    );
  }
  private sync() {
    const s = this.play.getSnapshot(),
      scope = this.authorizedScope(s);
    if (scope !== this.scope) {
      this.abortCapture();
      this.stopNarration();
      this.scope = scope;
      this.seen = new Set(s.entries.map((e) => e.id));
      this.patch({
        scope,
        capture: "idle",
        transcript: "",
        partial: false,
        error: null,
      });
    }
    const pending = s.entries.find(
      (e) => !e.action || ["submitted", "resolving"].includes(e.action.status),
    );
    const stage = pending
      ? pending.action?.status === "resolving"
        ? "resolving"
        : "interpreting"
      : s.entries.some((e) => e.narration?.status === "provisional")
        ? "narrating"
        : "idle";
    this.patch({ stage });
    if (!scope) return;
    if (
      s.busy &&
      ["requesting", "listening", "interpreting"].includes(this.state.capture)
    ) {
      this.abortCapture();
      this.patch({ capture: this.state.transcript ? "review" : "idle" });
    }
    // Never queue old or private audio. Only a new, complete, authorized narration can auto-play.
    for (const entry of s.entries) {
      if (!this.eligible(entry) || this.seen.has(entry.id)) continue;
      this.seen.add(entry.id);
      if (
        this.state.narrationEnabled &&
        !this.state.muted &&
        this.state.capture === "idle"
      )
        this.speak(entry);
    }
  }
  canListen() {
    return (
      !!this.scope &&
      this.speech.recognitionAvailable &&
      this.play.canSend(this.channel === "ooc" ? "question" : "text")
    );
  }
  start() {
    this.sync();
    if (!this.canListen() || this.state.capture !== "idle") return;
    this.stopNarration();
    const serial = ++this.captureSerial;
    this.patch({
      capture: "requesting",
      transcript: "",
      partial: false,
      error: null,
    });
    const active = () => serial === this.captureSerial && this.scope !== null;
    try {
      const handle = this.speech.listen({
        started: () => {
          if (active() && this.state.capture === "requesting")
            this.patch({ capture: "listening" });
        },
        transcript: (text, partial) => {
          if (active()) this.patch({ transcript: text, partial });
        },
        ended: () => {
          if (active()) this.finish();
        },
        failed: (code) => {
          if (active())
            this.finish(
              recognitionErrors[code] ??
                "Speech recognition failed. Review any transcript or use text, then try again.",
            );
        },
      });
      // Permission errors can be synchronous in an adapter; do not retain its handle.
      if (active()) {
        this.captureHandle = handle;
        this.timer = setTimeout(
          () =>
            this.finish(
              "Listening timed out. Review any partial transcript or try again.",
            ),
          60000,
        );
      } else handle.abort();
    } catch {
      if (active())
        this.finish(
          "Could not start the microphone. Check browser permissions or use text.",
        );
    }
  }
  stop() {
    if (!["requesting", "listening"].includes(this.state.capture)) return;
    this.patch({ capture: "interpreting" });
    if (this.timer) clearTimeout(this.timer);
    const serial = this.captureSerial;
    this.timer = setTimeout(() => {
      if (serial === this.captureSerial)
        this.finish(
          "Recognition did not finish. Review the partial transcript or try again.",
        );
    }, 5000);
    try {
      this.captureHandle?.stop();
    } catch {
      this.finish(
        "Listening stopped. Review any partial transcript or try again.",
      );
    }
  }
  private finish(error: string | null = null) {
    this.abortCapture();
    this.patch({
      capture: this.state.transcript.trim() ? "review" : "idle",
      error:
        error ??
        (this.state.transcript.trim() ? null : recognitionErrors["no-speech"]!),
    });
  }
  private abortCapture() {
    ++this.captureSerial;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const capture = this.captureHandle;
    this.captureHandle = null;
    try {
      capture?.abort();
    } catch {
      /* Callback fencing still prevents late private results. */
    }
  }
  discard() {
    this.abortCapture();
    this.patch({
      capture: "idle",
      transcript: "",
      partial: false,
      error: null,
    });
  }
  edit(text: string) {
    if (this.state.capture === "review") this.patch({ transcript: text });
  }
  async submit() {
    this.sync();
    const max = this.channel === "dialogue" ? 1975 : 2000;
    const text = this.state.transcript.trim();
    if (
      !this.scope ||
      this.state.capture !== "review" ||
      !text ||
      text.length > max ||
      !this.play.canSend(this.channel === "ooc" ? "question" : "text")
    )
      return;
    // Consume the review synchronously. Repeated clicks cannot create another action identity.
    this.discard();
    await this.play.send(this.channel, text, undefined, {
      preserveDraft: true,
    });
  }
  enableNarration(enabled: boolean) {
    this.patch({ narrationEnabled: enabled && this.speech.narrationAvailable });
    if (!enabled) this.stopNarration();
  }
  mute(muted: boolean) {
    this.patch({ muted });
    if (muted) this.stopNarration();
  }
  stopNarration() {
    ++this.speechSerial;
    const cancel = this.cancelSpeech;
    this.cancelSpeech = null;
    try {
      cancel?.();
    } catch {
      /* Local audio failure must never affect the action. */
    }
    this.patch({ speaking: false });
  }
  replay() {
    this.sync();
    const entry = [...this.play.getSnapshot().entries]
      .reverse()
      .find((e) => this.eligible(e));
    if (
      entry &&
      this.scope &&
      this.state.capture === "idle" &&
      !this.state.muted &&
      this.state.narrationEnabled
    )
      this.speak(entry);
  }
  private speak(entry: Entry) {
    this.stopNarration();
    const serial = this.speechSerial;
    this.patch({ speaking: true });
    try {
      const cancel = this.speech.speak(
        entry.narration!.text,
        () => {
          if (serial === this.speechSerial) {
            this.cancelSpeech = null;
            this.patch({ speaking: false });
          }
        },
        () => {
          if (serial === this.speechSerial) {
            this.stopNarration();
            this.patch({
              error:
                "Spoken narration failed. The committed result and written narration are still available.",
            });
          }
        },
      );
      if (serial === this.speechSerial && this.state.speaking)
        this.cancelSpeech = cancel;
      else cancel();
    } catch {
      this.patch({
        speaking: false,
        error:
          "Spoken narration is unavailable. Read the written narration instead.",
      });
    }
  }
  suspend() {
    this.discard();
    this.stopNarration();
  }
  dispose() {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.suspend();
    this.scope = null;
    this.seen.clear();
  }
}
