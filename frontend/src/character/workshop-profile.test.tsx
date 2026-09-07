import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { LiveTransport } from "../play/live";
import { CharacterWorkshop } from "./workshop";

const transport = new LiveTransport("alice", "campaign", "token");
vi.mock("../play/use-play", () => ({
  usePlay: () => ({ store: { transport }, state: { actorId: "a" } }),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("treats another version of the same profile as preview-only, and uses the campaign compiler for its exact version", async () => {
  const profile = {
    id: "profile:example",
    version: 2,
    title: "Example",
    supported: true,
    blockers: [],
  };
  const preview = {
    catalog: [],
    spent: 15,
    remaining: 85,
    legal: true,
    diagnostics: [],
    derived: [],
    breakdown: [],
  };
  const request = vi
    .spyOn(transport, "request")
    .mockImplementation(async (path) => {
      if (path === "/workshop/a")
        return {
          options: {
            active_profile: profile.id,
            active_profile_version: 2,
            profiles: [profile, { ...profile, version: 3 }],
            catalog: [],
            build_revision: "build",
            points_available: 0,
            can_approve: false,
            can_edit: true,
          },
          proposal: {
            draft: { name: "Hero", backstory: "", purchases: [] },
            custom: [],
          },
          revision: 0,
          draft: null,
          catalog: [],
        };
      if (path === "/workshop-profile-preview")
        return { ...preview, profile: { ...profile, version: 3 } };
      return preview;
    });
  render(<CharacterWorkshop />);
  const select = await screen.findByLabelText("Rules profile");
  await userEvent.selectOptions(select, "profile:example@3");
  expect(
    screen.getByRole("button", { name: "Save and validate" }),
  ).toBeDisabled();
  await screen.findByText(
    "Preview only. Changing the campaign profile requires an explicit migration.",
  );
  await userEvent.selectOptions(select, "profile:example@2");
  expect(
    screen.getByRole("button", { name: "Save and validate" }),
  ).toBeEnabled();
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      "/workshop/a/preview",
      expect.any(AbortSignal),
      expect.objectContaining({ proposal: expect.any(Object) }),
    ),
  );
  expect(
    request.mock.calls.filter(([path]) => path === "/workshop-profile-preview"),
  ).toHaveLength(1);
});
