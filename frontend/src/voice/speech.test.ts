import { afterEach, describe, expect, it, vi } from "vitest";
import { browserSpeech, type NativeRecognition } from "./speech";

class Recognition implements NativeRecognition {
  static instances: Recognition[] = [];
  lang = "";
  continuous = false;
  interimResults = false;
  onstart: (() => void) | null = null;
  onresult:
    | ((event: {
        results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
      }) => void)
    | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn(() => this.onstart?.());
  stop = vi.fn(() => this.onend?.());
  abort = vi.fn();
  constructor() {
    Recognition.instances.push(this);
  }
}

const originalRecognition = Object.getOwnPropertyDescriptor(
  window,
  "SpeechRecognition",
);
const originalWebkit = Object.getOwnPropertyDescriptor(
  window,
  "webkitSpeechRecognition",
);
const originalSecure = Object.getOwnPropertyDescriptor(
  window,
  "isSecureContext",
);

function define(name: string, value: unknown) {
  Object.defineProperty(window, name, { configurable: true, value });
}

afterEach(() => {
  Recognition.instances = [];
  for (const [name, descriptor] of [
    ["SpeechRecognition", originalRecognition],
    ["webkitSpeechRecognition", originalWebkit],
    ["isSecureContext", originalSecure],
  ] as const) {
    if (descriptor) Object.defineProperty(window, name, descriptor);
    else Reflect.deleteProperty(window, name);
  }
  vi.restoreAllMocks();
});

describe("browser speech adapter", () => {
  it.each(["SpeechRecognition", "webkitSpeechRecognition"])(
    "adapts %s hypotheses without executing them",
    (constructor) => {
      define("SpeechRecognition", undefined);
      define("webkitSpeechRecognition", undefined);
      define(constructor, Recognition);
      define("isSecureContext", true);
      const transcript = vi.fn();
      const ended = vi.fn();
      const capture = browserSpeech().listen({
        started: vi.fn(),
        transcript,
        ended,
        failed: vi.fn(),
      });
      const recognition = Recognition.instances[0]!;
      expect(recognition).toMatchObject({
        continuous: true,
        interimResults: true,
      });
      recognition.onresult?.({
        results: [
          { isFinal: true, 0: { transcript: "Open" } },
          { isFinal: false, 0: { transcript: "the door" } },
        ],
      });
      expect(transcript).toHaveBeenCalledWith("Open the door", true);
      capture.stop();
      expect(ended).toHaveBeenCalledOnce();
      expect(recognition.onresult).toBeNull();
    },
  );

  it("fails closed outside a secure context and leaves text fallback available", () => {
    define("SpeechRecognition", Recognition);
    define("isSecureContext", false);
    const speech = browserSpeech();
    expect(speech.recognitionAvailable).toBe(false);
    expect(() =>
      speech.listen({
        started: vi.fn(),
        transcript: vi.fn(),
        ended: vi.fn(),
        failed: vi.fn(),
      }),
    ).toThrow("unavailable");
    expect(Recognition.instances).toHaveLength(0);
  });

  it("detaches late private callbacks when capture is aborted", () => {
    define("SpeechRecognition", Recognition);
    define("isSecureContext", true);
    const transcript = vi.fn();
    const capture = browserSpeech().listen({
      started: vi.fn(),
      transcript,
      ended: vi.fn(),
      failed: vi.fn(),
    });
    const recognition = Recognition.instances[0]!;
    capture.abort();
    expect(recognition.abort).toHaveBeenCalledOnce();
    expect(recognition.onresult).toBeNull();
    expect(transcript).not.toHaveBeenCalled();
  });
});
