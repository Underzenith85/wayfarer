import { afterEach, describe, expect, it, vi } from "vitest";
import { browserSpeech, type NativeRecognition } from "./speech";
afterEach(() => vi.unstubAllGlobals());
describe("browser speech adapter", () => {
  it("feature-detects unsupported or insecure recognition without asking permission", () => {
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    expect(browserSpeech().recognitionAvailable).toBe(false);
    vi.stubGlobal("SpeechRecognition", class {});
    vi.stubGlobal("isSecureContext", false);
    expect(browserSpeech().recognitionAvailable).toBe(false);
  });
  it("replaces aggregate results and detaches all handlers on abort", () => {
    const instances: NativeRecognition[] = [];
    const start = vi.fn(),
      stop = vi.fn(),
      abort = vi.fn();
    class Recognition implements NativeRecognition {
      lang = "";
      continuous = false;
      interimResults = false;
      onstart: NativeRecognition["onstart"] = null;
      onend: NativeRecognition["onend"] = null;
      onresult: NativeRecognition["onresult"] = null;
      onerror: NativeRecognition["onerror"] = null;
      start = start;
      stop = stop;
      abort = abort;
      constructor() {
        instances.push(this);
      }
    }
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", Recognition);
    vi.stubGlobal("isSecureContext", true);
    const events = {
      started: vi.fn(),
      ended: vi.fn(),
      failed: vi.fn(),
      transcript: vi.fn(),
    };
    const capture = browserSpeech().listen(events);
    const recognition = instances[0]!;
    expect(start).toHaveBeenCalledOnce();
    expect(recognition!.continuous).toBe(true);
    expect(recognition!.interimResults).toBe(true);
    recognition!.onresult!({
      results: [
        { isFinal: true, 0: { transcript: "Open" } },
        { isFinal: false, 0: { transcript: "the door" } },
      ],
    });
    expect(events.transcript).toHaveBeenCalledWith("Open the door", true);
    capture.stop();
    expect(stop).toHaveBeenCalledOnce();
    capture.abort();
    expect(abort).toHaveBeenCalledOnce();
    expect(recognition!.onresult).toBeNull();
    expect(recognition!.onend).toBeNull();
    expect(recognition!.onerror).toBeNull();
  });
});
