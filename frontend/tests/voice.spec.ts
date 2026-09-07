import { test, expect, type Locator, type Page } from "@playwright/test";

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
/** The browser-speech notice is shown once; tests that are not about it start past it. */
async function acknowledge(page: Page) {
  await page.addInitScript(() =>
    localStorage.setItem("wayfarer-voice-notice", "seen"),
  );
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
  await expect(page.getByLabel("What do you do?")).toBeVisible();
}
/** Narration playback lives beside the transcript, not in the composer (#195). */
async function narration(page: Page, act: (sheet: Locator) => Promise<void>) {
  await page.getByRole("button", { name: "Narration", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await act(sheet);
  await sheet.getByRole("button", { name: "Close details" }).click();
  await expect(sheet).toHaveCount(0);
}
/** A tap on the mic latches listening on; a second tap ends it. */
async function capture(page: Page, text: string) {
  await page.getByRole("button", { name: "Start voice input" }).click();
  await page.evaluate((text) => window.voiceHarness.emit(text), text);
  await page.getByRole("button", { name: "Stop voice input" }).click();
  await expect(page.getByLabel("What do you do?")).toHaveValue(text);
}
test("first use of the mic discloses browser speech before any microphone starts", async ({
  page,
}) => {
  await mockSpeech(page);
  await open(page);
  // The disclosure is not permanent body copy above the input: it is the mic's
  // description until the mic is used.
  await expect(
    page.getByRole("dialog", { name: "About browser speech" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Start voice input" }).click();
  const notice = page.getByRole("dialog", { name: "About browser speech" });
  await expect(notice).toContainText("No microphone starts until you choose");
  expect(await page.evaluate(() => window.voiceHarness.starts)).toBe(0);
  await notice.getByRole("button", { name: "Start listening" }).click();
  await expect(notice).toHaveCount(0);
  expect(await page.evaluate(() => window.voiceHarness.starts)).toBe(1);
  await page.evaluate(() => window.voiceHarness.emit("Open the shutter"));
  await page.getByRole("button", { name: "Stop voice input" }).click();
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "Open the shutter",
  );
  // Acknowledged once: the next capture starts the microphone directly.
  await page.getByRole("button", { name: "Discard voice input" }).click();
  await page.getByRole("button", { name: "Start voice input" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => window.voiceHarness.starts)).toBe(2);
});
test("partial push-to-talk requires edited review, submits once and interrupts only audio after commit", async ({
  page,
}) => {
  await mockSpeech(page);
  await acknowledge(page);
  await open(page);
  await narration(page, (sheet) =>
    sheet.getByLabel("Speak new completed narration").check(),
  );
  await page.getByLabel("What do you do?").fill("Keep this typed draft");
  const talk = page.getByRole("button", { name: "Start voice input" });
  await talk.focus();
  await page.keyboard.down("Space");
  await expect(
    page.getByText("Listening — release to review", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => window.voiceHarness.emit("Use the"));
  await page.evaluate(() => window.voiceHarness.emit("Use the bandage"));
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
  // Held, not tapped: releasing ends the capture.
  await page.waitForTimeout(500);
  await page.keyboard.up("Space");
  // The transcript lands in the composer's own field, for review.
  await expect(page.getByLabel("What do you do?")).toHaveValue(
    "Use the bandage",
  );
  await page.getByLabel("What do you do?").fill("Dress the wound");
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
  await narration(page, async (sheet) => {
    await expect(
      sheet.getByRole("button", { name: "Interrupt narration" }),
    ).toBeEnabled();
    await sheet.getByRole("button", { name: "Interrupt narration" }).click();
    await expect(
      sheet.getByRole("button", { name: "Interrupt narration" }),
    ).toBeDisabled();
    expect(await page.evaluate(() => window.voiceHarness.spoken.length)).toBe(
      1,
    );
    await sheet
      .getByRole("button", { name: "Replay latest narration" })
      .click();
    await sheet
      .getByRole("button", { name: "Mute narration", exact: true })
      .click();
    expect(await page.evaluate(() => window.voiceHarness.cancels)).toBe(2);
    await expect(
      sheet.getByRole("button", { name: "Replay latest narration" }),
    ).toBeDisabled();
  });
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
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
  await acknowledge(page);
  await open(page);
  await page.getByRole("button", { name: "Start voice input" }).click();
  await expect(page.getByRole("alert")).toContainText("permission was denied");
  await page.getByLabel("What do you do?").fill("Use the bandage");
  await page.getByRole("button", { name: "Send action", exact: true }).click();
  await expect(page.getByText("Committed", { exact: true })).toBeVisible();
  const unsupported = await page.context().newPage();
  await mockSpeech(unsupported, "unsupported");
  await acknowledge(unsupported);
  await open(unsupported);
  await expect(
    unsupported.getByRole("button", { name: "Start voice input" }),
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
  await acknowledge(page);
  await open(page);
  await capture(page, "Look at the");
  await expect(page.getByRole("alert")).toContainText("network connection");
  await expect(page.locator(".transcript-entry")).toHaveCount(0);
  await page.getByRole("button", { name: "Discard voice input" }).click();
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
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
  await acknowledge(page);
  await open(page);
  await narration(page, (sheet) =>
    sheet.getByLabel("Speak new completed narration").check(),
  );
  await capture(page, "Dress the wound");
  await page.getByRole("button", { name: "Send reviewed action" }).click();
  await narration(page, (sheet) =>
    expect(
      sheet.getByRole("button", { name: "Interrupt narration" }),
    ).toBeEnabled(),
  );
  await page.getByRole("link", { name: "Campaign", exact: true }).click();
  expect(await page.evaluate(() => window.voiceHarness.cancels)).toBe(1);
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "Lights on the Sound" }) })
    .getByRole("button", { name: "Open campaign" })
    .click();
  await expect(
    page.getByRole("button", { name: "Start voice input" }),
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
  await page.getByRole("button", { name: "Start voice input" }).click();
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
  await expect(page.getByLabel("What do you do?")).toHaveValue("");
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
  await acknowledge(page);
  const room = crypto.randomUUID();
  await page.goto(`/campaign?multiplayer=rescuer&room=${room}`);
  await page
    .getByRole("button", { name: "Open campaign", exact: true })
    .click();
  await page.getByRole("button", { name: "Start voice input" }).click();
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
  await expect(page.getByRole("button", { name: /voice input/ })).toHaveCount(
    0,
  );
});
