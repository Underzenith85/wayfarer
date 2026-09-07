import { describe, expect, it, vi } from "vitest";
import { VoiceSession, type TranscriptPort, type SpeechPort } from "./session";
import { PlayStore } from "../play/store";
import { FixtureTransport } from "../play/fixtures";
class Input implements TranscriptPort {
  handlers?: Parameters<TranscriptPort["start"]>[0];
  stop = vi.fn();
  abort = vi.fn();
  start(handlers: Parameters<TranscriptPort["start"]>[0]) {
    this.handlers = handlers;
  }
}
class Output implements SpeechPort {
  speak = vi.fn();
  cancel = vi.fn();
}
describe("scoped voice review", () => {
  it("requires explicit review of partial results and ignores late callbacks", () => {
    const input = new Input(),
      output = new Output(),
      voice = new VoiceSession(input, output);
    voice.listen();
    input.handlers!.result("wait ten", false);
    expect(voice.take()).toBe("");
    voice.stop();
    input.handlers!.result("wait ten hours", true);
    expect(voice.getSnapshot().text).toBe("wait ten");
    expect(voice.getSnapshot().partial).toBe(true);
    voice.edit("wait one minute");
    expect(voice.take()).toBe("wait one minute");
    expect(voice.take()).toBe("");
  });
  it("discards private input and speech on scene disposal, never cancelling a peer", () => {
    const input = new Input(),
      output = new Output(),
      peerOutput = new Output();
    const captive = new VoiceSession(input, output),
      rescuer = new VoiceSession(new Input(), peerOutput);
    rescuer.speak("Private rescue plan");
    captive.listen();
    input.handlers!.result("Secret cell", true);
    captive.cancel();
    input.handlers!.result("Late private result", true);
    input.handlers!.end();
    expect(captive.getSnapshot().text).toBe("");
    expect(captive.getSnapshot().phase).toBe("idle");
    expect(rescuer.getSnapshot().phase).toBe("narrating");
    expect(peerOutput.cancel).toHaveBeenCalledTimes(1); // only rescuer's own initial reset
  });
  it("keeps partial text reviewable on permission/network errors and can restart", () => {
    const input = new Input(),
      voice = new VoiceSession(input, null);
    voice.listen();
    input.handlers!.result("wait", false);
    input.handlers!.error("Permission denied");
    input.handlers!.end();
    expect(voice.getSnapshot().phase).toBe("error");
    expect(voice.take()).toBe("wait");
    voice.listen();
    expect(voice.getSnapshot().phase).toBe("listening");
    const unsupported = new VoiceSession(null, null);
    unsupported.listen();
    expect(unsupported.getSnapshot().error).toContain("Type");
  });
  it("speech interruption cannot resubmit an action", async () => {
    const input = new Input(),
      output = new Output(),
      voice = new VoiceSession(input, output);
    voice.speak("Committed narration");
    voice.listen();
    expect(output.cancel).toHaveBeenCalled();
    input.handlers!.result("Look around", true);
    input.handlers!.end();
    const reviewed = voice.take();
    const typed = new FixtureTransport("resolve", 0),
      spoken = new FixtureTransport("resolve", 0);
    const textStore = new PlayStore(typed, () => {}, 1),
      voiceStore = new PlayStore(spoken, () => {}, 1);
    const textSend = vi.spyOn(typed, "submitAction"),
      voiceSend = vi.spyOn(spoken, "submitAction");
    await textStore.loadCampaigns();
    await voiceStore.loadCampaigns();
    await textStore.select(textStore.getSnapshot().campaigns[0]!.id);
    await voiceStore.select(voiceStore.getSnapshot().campaigns[0]!.id);
    await textStore.send("action", "Look around");
    await voiceStore.send("action", reviewed);
    expect(voiceSend.mock.calls[0]![1].intent).toEqual(
      textSend.mock.calls[0]![1].intent,
    );
    voice.cancel();
    expect(voiceSend).toHaveBeenCalledTimes(1);
    textStore.dispose();
    voiceStore.dispose();
  });
});
