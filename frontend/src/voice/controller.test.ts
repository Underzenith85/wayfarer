import { afterEach, describe, expect, it, vi } from "vitest";
import { PlayStore } from "../play/store";
import { FixtureTransport, type Journey } from "../play/fixtures";
import { VoiceController } from "./controller";
import type { RecognitionEvents, SpeechPort } from "./speech";

class FakeSpeech implements SpeechPort {
  recognitionAvailable = true;
  narrationAvailable = true;
  events: RecognitionEvents | null = null;
  stop = vi.fn();
  abort = vi.fn();
  cancel = vi.fn();
  spoken: string[] = [];
  ended: (() => void) | null = null;
  failed: (() => void) | null = null;
  listen(events: RecognitionEvents) {
    this.events = events;
    events.started();
    return { stop: this.stop, abort: this.abort };
  }
  speak(text: string, ended: () => void, failed: () => void) {
    this.spoken.push(text);
    this.ended = ended;
    this.failed = failed;
    return this.cancel;
  }
}
const cleanup: (() => void)[] = [];
afterEach(() => {
  cleanup.splice(0).forEach((fn) => fn());
  vi.useRealTimers();
});
async function start(journey: Journey = "resolve") {
  const transport = new FixtureTransport(journey, 1);
  const play = new PlayStore(transport, () => {}, 1);
  await play.select("campaign-1");
  const speech = new FakeSpeech();
  const voice = new VoiceController(play, speech, "action");
  cleanup.push(voice.connect(), () => play.dispose());
  return { transport, play, speech, voice };
}
function review(
  voice: VoiceController,
  speech: FakeSpeech,
  text = "Use the bandage",
) {
  voice.start();
  speech.events!.transcript(text, true);
  voice.stop();
  speech.events!.ended();
}
describe("reviewed, scope-bound voice", () => {
  it("never submits partial speech, replaces hypotheses and requires explicit edited review", async () => {
    const { voice, speech, transport, play } = await start();
    voice.start();
    expect(voice.getSnapshot().capture).toBe("listening");
    speech.events!.transcript("Use", true);
    speech.events!.transcript("Use the bandage", true);
    await voice.submit();
    expect(transport.requests).toHaveLength(0);
    voice.stop();
    expect(speech.stop).toHaveBeenCalledOnce();
    expect(voice.getSnapshot().capture).toBe("interpreting");
    speech.events!.ended();
    expect(voice.getSnapshot()).toMatchObject({
      capture: "review",
      partial: true,
      transcript: "Use the bandage",
    });
    voice.edit("Dress the wound");
    play.saveDraft("action", "An unrelated typed draft");
    await Promise.all([voice.submit(), voice.submit()]);
    expect(transport.requests).toHaveLength(1);
    expect(transport.requests[0]!.request).toMatchObject({
      actor_id: "hero-1",
      scene_id: "cellar-1",
      intent: { kind: "text", text: "Dress the wound" },
    });
    expect(play.getSnapshot().drafts.action).toBe("An unrelated typed draft");
    expect(voice.getSnapshot().transcript).toBe("");
  });
  it.each(["not-allowed", "audio-capture", "network", "no-speech"])(
    "recovers %s without submission or automatic microphone restart",
    async (code) => {
      const { voice, speech, transport } = await start();
      voice.start();
      speech.events!.transcript("Check the", true);
      speech.events!.failed(code);
      expect(voice.getSnapshot()).toMatchObject({
        capture: "review",
        transcript: "Check the",
        partial: true,
      });
      expect(voice.getSnapshot().error).toBeTruthy();
      expect(speech.abort).toHaveBeenCalledOnce();
      expect(transport.requests).toHaveLength(0);
      voice.discard();
      voice.start();
      expect(voice.getSnapshot().capture).toBe("listening");
    },
  );
  it("keeps text available with unsupported speech and rejects oversized reviews", async () => {
    const { voice, speech, play, transport } = await start();
    speech.recognitionAvailable = false;
    voice.start();
    expect(speech.events).toBeNull();
    expect(play.canSend("text")).toBe(true);
    speech.recognitionAvailable = true;
    review(voice, speech, "x".repeat(2001));
    await voice.submit();
    expect(transport.requests).toHaveLength(0);
    voice.edit("Inspect the door");
    await voice.submit();
    expect(transport.requests).toHaveLength(1);
  });
  it("uses the same action receipt on network retry", async () => {
    const { voice, speech, transport, play } = await start("retry");
    review(voice, speech);
    await voice.submit();
    expect(play.getSnapshot().retry).not.toBeNull();
    await voice.submit();
    expect(transport.requests).toHaveLength(1);
    await play.retry();
    expect(transport.requests[1]!.request).toEqual(
      transport.requests[0]!.request,
    );
  });
  it("interrupts local narration after commit without changing the action or another player", async () => {
    const { voice, speech, play, transport } = await start();
    const other = await start();
    voice.enableNarration(true);
    review(voice, speech);
    await voice.submit();
    expect(speech.spoken).toHaveLength(1);
    expect(voice.getSnapshot().speaking).toBe(true);
    const committed = structuredClone(play.getSnapshot().entries[0]);
    voice.stopNarration();
    expect(speech.cancel).toHaveBeenCalledOnce();
    expect(play.getSnapshot().entries[0]).toEqual(committed);
    expect(transport.requests).toHaveLength(1);
    expect(other.speech.cancel).not.toHaveBeenCalled();
    expect(other.play.canSend("text")).toBe(true);
    voice.replay();
    expect(speech.spoken).toHaveLength(2);
    voice.mute(true);
    voice.replay();
    expect(speech.spoken).toHaveLength(2);
    voice.mute(false);
    expect(speech.spoken).toHaveLength(2);
  });
  it("does not speak without opt-in, on provisional output, or after narration failure", async () => {
    const { voice, speech } = await start();
    review(voice, speech);
    await voice.submit();
    expect(speech.spoken).toHaveLength(0);
    voice.enableNarration(true);
    expect(speech.spoken).toHaveLength(0);
    const failed = await start("narration-failure");
    failed.voice.enableNarration(true);
    review(failed.voice, failed.speech);
    await failed.voice.submit();
    expect(failed.speech.spoken).toHaveLength(0);
  });
  it.each(["switch", "disconnect", "revoke", "dispose"])(
    "clears private capture and rejects late callbacks on %s",
    async (kind) => {
      const { voice, speech, play, transport } = await start();
      voice.start();
      speech.events!.transcript("Private plan", true);
      const old = speech.events!;
      if (kind === "switch") await play.select("campaign-2");
      else if (kind === "disconnect") play.disconnect();
      else if (kind === "revoke") play.expire();
      else voice.dispose();
      old.transcript("Late private words", false);
      old.ended();
      expect(speech.abort).toHaveBeenCalled();
      expect(voice.getSnapshot().transcript).toBe("");
      await voice.submit();
      expect(transport.requests).toHaveLength(0);
    },
  );
  it("stops private audio on scene changes and ignores late speech completion", async () => {
    const { voice, speech, play } = await start();
    voice.enableNarration(true);
    review(voice, speech);
    await voice.submit();
    const ended = speech.ended!;
    await play.select("campaign-2");
    expect(speech.cancel).toHaveBeenCalled();
    ended();
    voice.replay();
    expect(voice.getSnapshot().speaking).toBe(false);
    expect(speech.spoken).toHaveLength(1);
  });
  it("bounds microphone and finalization waits, preserving partial speech for review", async () => {
    const { voice, speech } = await start();
    vi.useFakeTimers();
    voice.start();
    speech.events!.transcript("Partial words", true);
    voice.stop();
    vi.advanceTimersByTime(5000);
    expect(voice.getSnapshot()).toMatchObject({
      capture: "review",
      transcript: "Partial words",
    });
    expect(voice.getSnapshot().error).toContain("did not finish");
    voice.discard();
    voice.start();
    vi.advanceTimersByTime(60000);
    expect(voice.getSnapshot().capture).toBe("idle");
    expect(voice.getSnapshot().error).toContain("timed out");
  });
  it.each(["scene", "membership"])(
    "invalidates a pending review on same-campaign %s changes",
    async (kind) => {
      const { voice, speech, play, transport } = await start();
      review(voice, speech, "Private words from the previous scope");
      const snapshot = structuredClone(play.getSnapshot().snapshot!);
      if (kind === "scene") snapshot.scene.id = "another-scene";
      else snapshot.campaign.membership.version = "new-grant";
      vi.spyOn(transport, "readSnapshot").mockResolvedValue(snapshot);
      await play.refresh();
      expect(voice.getSnapshot().capture).toBe("idle");
      expect(voice.getSnapshot().transcript).toBe("");
      await voice.submit();
      expect(transport.requests).toHaveLength(0);
    },
  );
});
