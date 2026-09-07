import { test, expect, type Page } from "@playwright/test";

interface VoiceHarness {
  starts: number;
  aborts: number;
  cancels: number;
  spoken: string[];
  mode: string;
  emit(text: string, final?: boolean): void;
  late(text: string): void;
}
declare global {
  interface Window {
    voiceHarness: VoiceHarness;
  }
}
async function mockSpeech(page: Page, mode = "success") {
  await page.addInitScript((mode) => {
    type Results = {
      results: { isFinal: boolean; 0: { transcript: string } }[];
    };
    let emitResult: ((e: Results) => void) | null = null;
    let lateResult: ((e: Results) => void) | null = null;
    const harness: VoiceHarness = {
      starts: 0,
      aborts: 0,
      cancels: 0,
      spoken: [],
      mode,
      emit(text, final = false) {
        emitResult?.({
          results: [{ isFinal: final, 0: { transcript: text } }],
        });
      },
      late(text) {
        lateResult?.({ results: [{ isFinal: true, 0: { transcript: text } }] });
      },
    };
    class Recognition {
      lang = "";
      continuous = false;
      interimResults = false;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: ((e: { error: string }) => void) | null = null;
      onresult: ((e: Results) => void) | null = null;
      start() {
        emitResult = (event) => this.onresult?.(event);
        harness.starts++;
        lateResult = this.onresult;
        if (harness.mode === "denied") this.onerror?.({ error: "not-allowed" });
        else this.onstart?.();
      }
      stop() {
        if (harness.mode === "network") this.onerror?.({ error: "network" });
        else this.onend?.();
      }
      abort() {
        harness.aborts++;
      }
    }
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: mode === "unsupported" ? undefined : Recognition,
    });
    Object.defineProperty(window, "webkitSpeechRecognition", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      configurable: true,
      value: class {
        constructor(public text: string) {}
      },
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        speak(utterance: { text: string }) {
          harness.spoken.push(utterance.text);
        },
        cancel() {
          harness.cancels++;
        },
      },
    });
    window.voiceHarness = harness;
  }, mode);
}
async function open(page: Page, query = "journey=resolve") {
  await page.goto(`/campaign?${query}`);
  await page
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", {
        name: "The Missing Courier",
        exact: true,
      }),
    })
    .getByRole("button", { name: /^(Open|Resume) campaign$/ })
    .click();
  await expect(
    page.getByRole("region", { name: "Voice controls" }),
  ).toBeVisible();
}
async function capture(page: Page, text: string) {
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page.evaluate((text) => window.voiceHarness.emit(text), text);
  await page
    .getByRole("button", { name: "Stop listening", exact: true })
    .click();
  await expect(page.getByLabel("Review voice transcript")).toHaveValue(text);
}
test("partial push-to-talk requires edited review, submits once and interrupts only audio after commit", async ({
  page,
}) => {
  await mockSpeech(page);
  await open(page);
  await page.getByLabel("Speak new completed narration").check();
  await page.getByLabel("What do you do?").fill("Keep this typed draft");
  const talk = page.getByRole("button", { name: "Hold to talk", exact: true });
  await talk.focus();
  await page.keyboard.down("Space");
  await expect(
    page.getByText("Listening — release to review", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => window.voiceHarness.emit("Use the"));
  await page.evaluate(() => window.voiceHarness.emit("Use the bandage"));
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
  await page.keyboard.up("Space");
  await expect(page.getByLabel("Review voice transcript")).toHaveValue(
    "Use the bandage",
  );
  await page.getByLabel("Review voice transcript").fill("Dress the wound");
  await page
    .getByRole("button", { name: "Send reviewed action" })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  await expect(page.locator(".transcript-entry")).toHaveCount(1);
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "Keep this typed draft",
  );
  await expect(
    page.getByRole("button", { name: "Interrupt narration" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Interrupt narration" }).click();
  await expect(
    page.getByRole("button", { name: "Interrupt narration" }),
  ).toBeDisabled();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.voiceHarness.spoken.length)).toBe(1);
  await page.getByRole("button", { name: "Replay latest narration" }).click();
  await page
    .getByRole("button", { name: "Mute narration", exact: true })
    .click();
  expect(await page.evaluate(() => window.voiceHarness.cancels)).toBe(2);
  await expect(
    page.getByRole("button", { name: "Replay latest narration" }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("denied and unsupported microphones retain usable text fallback", async ({
  page,
}) => {
  await mockSpeech(page, "denied");
  await open(page);
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("permission was denied");
  await page.getByLabel("What do you do?").fill("Use the bandage");
  await page.getByRole("button", { name: "Send action", exact: true }).click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  const unsupported = await page.context().newPage();
  await mockSpeech(unsupported, "unsupported");
  await open(unsupported);
  await expect(
    unsupported.getByRole("button", { name: "Hold to talk" }),
  ).toBeDisabled();
  await expect(
    unsupported.getByText(/Microphone speech recognition is unsupported/),
  ).toBeVisible();
  await unsupported.getByLabel("What do you do?").fill("Look at the door");
  await expect(
    unsupported.getByRole("button", { name: "Send action", exact: true }),
  ).toBeEnabled();
});
test("recognition network failure preserves partial review without auto-submission", async ({
  page,
}) => {
  await mockSpeech(page, "network");
  await open(page);
  await capture(page, "Look at the");
  await expect(page.getByRole("alert")).toContainText("network connection");
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
  await page.getByRole("button", { name: "Discard voice input" }).click();
  await page.evaluate(() => {
    window.voiceHarness.mode = "success";
  });
  await capture(page, "Look at the door");
  await page.getByRole("button", { name: "Send reviewed action" }).click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
});
test("campaign switch clears partial speech and stops private narration", async ({
  page,
}) => {
  await mockSpeech(page);
  await open(page);
  await page.getByLabel("Speak new completed narration").check();
  await capture(page, "Dress the wound");
  await page.getByRole("button", { name: "Send reviewed action" }).click();
  await expect(
    page.getByRole("button", { name: "Interrupt narration" }),
  ).toBeEnabled();
  await page.getByRole("link", { name: "Campaign", exact: true }).click();
  expect(await page.evaluate(() => window.voiceHarness.cancels)).toBe(1);
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "Lights on the Sound" }) })
    .getByRole("button", { name: "Open campaign" })
    .click();
  await expect(
    page.getByRole("button", { name: "Start listening", exact: true }),
  ).toBeDisabled();
  await page.getByRole("link", { name: "Campaign", exact: true }).click();
  await page
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", {
        name: "The Missing Courier",
        exact: true,
      }),
    })
    .getByRole("button", { name: /^(Open|Resume) campaign$/ })
    .click();
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page.evaluate(() => window.voiceHarness.emit("Private harbor plan"));
  await page.getByRole("link", { name: "Campaign", exact: true }).click();
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "Lights on the Sound" }) })
    .getByRole("button", { name: /^(Open|Resume) campaign$/ })
    .click();
  await page.evaluate(() =>
    window.voiceHarness.late("Late private harbor words"),
  );
  await expect(page.getByLabel("Review voice transcript")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("Private harbor plan");
  await expect(page.locator("body")).not.toContainText(
    "Late private harbor words",
  );
});
test("revoked scene access aborts the microphone and erases review buffers", async ({
  page,
  request,
}) => {
  await mockSpeech(page);
  const room = crypto.randomUUID();
  await page.goto(`/campaign?multiplayer=rescuer&room=${room}`);
  await page
    .getByRole("button", { name: "Open campaign", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page.evaluate(() => window.voiceHarness.emit("Private rescue plan"));
  const response = await request.post("/__fixtures/multiplayer", {
    headers: { "x-mock-room": room, "x-mock-identity": "rescuer" },
    data: { op: "scenario", scenario: "revoke" },
  });
  expect(response.ok()).toBe(true);
  await expect(
    page.getByRole("heading", { name: "Session ended", exact: true }),
  ).toBeVisible();
  await page.evaluate(() =>
    window.voiceHarness.late("A revoked late transcript"),
  );
  expect(await page.evaluate(() => window.voiceHarness.aborts)).toBeGreaterThan(
    0,
  );
  await expect(page.locator("body")).not.toContainText("Private rescue plan");
  await expect(
    page.getByRole("region", { name: "Voice controls" }),
  ).toHaveCount(0);
});
