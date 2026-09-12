import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  TacticalClient,
  parseTactical,
  type TacticalCommand,
  type TacticalSnapshot,
} from "../api/tactical";
import { TacticalPanel } from "./tactical";

const command: Extract<TacticalCommand, { kind: "choose_defense" }> = {
  kind: "choose_defense",
  catch_thrown: false,
  basic_retreat: false,
  id: "same-receipt",
  actor_id: "a",
  encounter_id: "fight",
  expected_revision: 4,
  defense: "dodge",
  item_id: null,
  second_defense: null,
  second_item_id: null,
  retreat: null,
  parry_mode_id: null,
  second_parry_mode_id: null,
};
const snapshot: TacticalSnapshot = {
  equipment: [],
  version: "tactical-v2",
  campaign_id: "campaign",
  actor_id: "a",
  revision: 4,
  encounters: [
    {
      id: "fight",
      status: "active",
      round: 1,
      current_actor_id: "b",
      coordinate_system: "hex-axial-v1",
      notice: null,
      cells: [
        {
          position: { q: 0, r: 0 },
          elevation: 0,
          blocked: false,
          extra_cost: 0,
          opaque_height: 0,
        },
      ],
      actors: [
        {
          id: "a",
          name: "Arin",
          position: { q: 0, r: 0 },
          facing: 0,
          posture: "standing",
          controlled: true,
          grappled: false,
          pinned: false,
        },
      ],
      grips: [],
      traces: [],
      choices: [{ label: "Dodge defense", command }],
    },
  ],
};

class FakeClient extends TacticalClient {
  reads = vi.fn(async () => structuredClone(snapshot));
  writes = vi.fn(async (value: TacticalCommand) => {
    void value;
    return structuredClone(snapshot);
  });
  constructor() {
    super("", "fixture-token");
  }
  override async read() {
    return this.reads();
  }
  override async execute(_cid: string, value: TacticalCommand) {
    return this.writes(value);
  }
}

describe("Tactical panel", () => {
  it("renders the server-filtered map and equivalent text/keyboard controls", async () => {
    const client = new FakeClient();
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    const button = await screen.findByRole("button", { name: "Dodge defense" });
    expect(
      screen.getByRole("img", { name: /Visible tactical hex map/ }),
    ).toBeVisible();
    expect(screen.getByText(/Arin \(you\): \(0, 0\)/)).toBeVisible();
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    await waitFor(() => expect(client.writes).toHaveBeenCalledWith(command));
  });
  it("shows ground equipment and submits the recorded retrieval choice", async () => {
    const client = new FakeClient();
    const retrieval: TacticalCommand = {
      kind: "retrieve_equipment",
      id: "retrieve",
      actor_id: "a",
      expected_revision: 4,
      encounter_id: "fight",
      item_id: "sword",
      stage: "start",
      task_id: null,
    };
    const equipped = structuredClone(snapshot);
    equipped.equipment = [
      {
        id: "sword",
        name: "Sword",
        condition: null,
        readiness: null,
        loaded_rounds: null,
        charges: null,
        ground: { encounter_id: "fight", geometry: "hex", x: -6, y: 0 },
        work: null,
        due_in: null,
        choices: [{ label: "Start retrieval", command: retrieval }],
      },
    ];
    client.reads.mockResolvedValue(equipped);
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    const button = await screen.findByRole("button", {
      name: "Start retrieval",
    });
    expect(screen.getByText(/Ground \(-6, 0\)/)).toBeVisible();
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    await waitFor(() => expect(client.writes).toHaveBeenCalledWith(retrieval));
  });
  it("retains the exact command after a lost response and blocks new commands", async () => {
    const client = new FakeClient();
    client.writes.mockRejectedValueOnce(new TypeError("Network response lost"));
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Dodge defense" }),
    );
    const retry = await screen.findByRole("button", {
      name: "Retry same action",
    });
    expect(
      screen.getByRole("button", { name: "Dodge defense" }),
    ).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(client.writes).toHaveBeenCalledTimes(2));
    expect(client.writes.mock.calls[0]?.[0]).toEqual(
      client.writes.mock.calls[1]?.[0],
    );
  });
  it("validates tactical responses and never accepts internal engine fields", () => {
    expect(parseTactical(snapshot)).toEqual(snapshot);
    expect(() =>
      parseTactical({ ...snapshot, world: { hidden: "secret" } }),
    ).toThrow(/Invalid tactical/);
    expect(() => parseTactical({ ...snapshot, revision: -1 })).toThrow(
      /Invalid tactical/,
    );
  });
});
