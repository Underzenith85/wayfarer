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
  migrations: [],
  withdrawals: [],
  basic_encounters: [],
  activity: {
    kind: "combat",
    representation: "hex",
    message: "Active hex combat.",
    ready_through: 4,
    paused: false,
  },
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
  it("offers and submits the GM representation conversion", async () => {
    const client = new FakeClient();
    const migration: TacticalCommand = {
      kind: "migrate_encounter_basic",
      id: "basic:4:fight",
      actor_id: "gm",
      expected_revision: 4,
      encounter_id: "fight",
    };
    const convertible = structuredClone(snapshot);
    convertible.migrations = [
      { label: "Convert to Basic combat", command: migration },
    ];
    client.reads.mockResolvedValue(convertible);
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Convert to Basic combat" }),
    );
    await waitFor(() => expect(client.writes).toHaveBeenCalledWith(migration));
  });
  it("offers a safe withdrawal even when Basic combat has no map projection", async () => {
    const client = new FakeClient();
    const withdrawal: TacticalCommand = {
      kind: "withdraw_encounter",
      id: "withdraw:a",
      actor_id: "a",
      expected_revision: 5,
      encounter_id: "fight",
      new_group_id: "independent:a",
    };
    const safe = structuredClone(snapshot);
    safe.encounters = [];
    safe.withdrawals = [{ label: "Leave combat", command: withdrawal }];
    client.reads.mockResolvedValue(safe);
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    expect(await screen.findByText(/safe departure boundary/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Leave combat" }));
    await waitFor(() => expect(client.writes).toHaveBeenCalledWith(withdrawal));
  });
  it("renders and submits server-previewed Basic combat choices without a map", async () => {
    const client = new FakeClient();
    const move: Extract<TacticalCommand, { kind: "take_combat_turn" }> = {
      kind: "take_combat_turn",
      id: "basic-move",
      actor_id: "a",
      expected_revision: 4,
      encounter_id: "fight",
      transport_id: null,
      maneuver: "move",
      destination: null,
      facing: null,
      posture: null,
      item_id: null,
      target_id: null,
      mode_id: null,
      shots: 1,
      spray_targets: [],
      suppression_zones: [],
      laser_sight: false,
      reload_ammunition_id: null,
      unload_ammunition: false,
      fast_draw: false,
      cocking_aid_id: null,
      let_down_bow: false,
      recover_thrown_item: false,
      escape_entanglement: false,
      mount_crew: [],
      firearm_service: null,
      firearm_service_skill: "weapon",
      hit_location: null,
      target_item_id: null,
      ready_hand: null,
      attack_option: null,
      defense_option: null,
      wait_trigger: null,
      step_timing: "before",
      second_item_id: null,
      second_target_id: null,
      second_mode_id: null,
      braced: false,
      hex_path: [],
      hex_facing: null,
      basic_move: { reference_actor_id: "b", direction: "approach" },
    };
    const basic = structuredClone(snapshot);
    basic.encounters = [];
    basic.activity = {
      kind: "combat",
      representation: "basic",
      message: "Active basic combat.",
      ready_through: 4,
      paused: false,
    };
    basic.basic_encounters = [
      {
        id: "fight",
        status: "active",
        round: 1,
        current_actor_id: "a",
        notice: "Spatial clarification is required for actions not listed.",
        actors: [
          {
            id: "a",
            name: "Arin",
            controlled: true,
            posture: "standing",
            grappled: false,
            pinned: false,
          },
          {
            id: "b",
            name: "Bandit",
            controlled: false,
            posture: "standing",
            grappled: false,
            pinned: false,
          },
        ],
        choices: [{ label: "Approach Bandit", command: move }],
      },
    ];
    client.reads.mockResolvedValue(basic);
    render(
      <TacticalPanel
        client={client}
        cid="campaign"
        actor="a"
        onChange={async () => {}}
      />,
    );
    expect(await screen.findByText(/Basic combat · round 1/)).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approach Bandit" }));
    await waitFor(() => expect(client.writes).toHaveBeenCalledWith(move));
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
