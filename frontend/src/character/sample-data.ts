import type { Snapshot } from "../play/transport";
import type { Affordance, InventoryOperation } from "./presentation";
const operations: InventoryOperation[] = [
  "inspect",
  "use_item",
  "equip",
  "drop",
  "transfer",
  "store",
];
const affordances = (usable: boolean): Affordance[] =>
  operations.map((kind) => ({
    kind,
    allowed: kind !== "use_item" || usable,
    reason:
      kind === "use_item" && !usable ? "This item cannot be consumed." : null,
  }));
/** Display-only, predetermined fixture projections; no costs or effects are calculated here. */
export function enrichSnapshot(source: Snapshot): Snapshot {
  const s = structuredClone(source);
  s.characterDetails = {};
  s.inventoryDetails = {};
  for (const character of s.characters) {
    character.attributes = [
      { id: "st", label: "ST", value: 10 },
      { id: "dx", label: "DX", value: 12 },
      { id: "iq", label: "IQ", value: 11 },
      { id: "ht", label: "HT", value: 10 },
    ];
    character.skills = [
      { id: "first-aid", label: "First Aid", value: 12 },
      { id: "stealth", label: "Stealth", value: 13 },
      { id: "observation", label: "Observation", value: 11 },
    ];
    character.defenses = [
      { id: "dodge", label: "Dodge", value: 8 },
      { id: "parry", label: "Parry", value: 9 },
    ];
    character.movement = [
      { id: "move", label: "Move", value: 4 },
      { id: "speed", label: "Basic Speed", value: 5.5 },
    ];
    character.conditions = [
      {
        id: "bruised",
        label: "Bruised",
        description:
          "A tender shoulder from the fall. Listed effects already appear in the authoritative stats.",
      },
    ];
    character.equipped_item_ids = ["coat-1"];
    s.characterDetails[character.id] = {
      rulesLabel: "Sample rules package · 100-point character",
      effects: [
        {
          id: "load",
          label: "Light encumbrance",
          description: "Move 4 is the current resolved movement value.",
          source: "Carried equipment",
        },
      ],
      points: {
        version: "ledger-1",
        buildTotal: 100,
        earned: 5,
        spent: 2,
        available: 3,
        ledger: [
          { id: "award-1", amount: 5, reason: "Completed the opening scene" },
          {
            id: "purchase-1",
            amount: -2,
            reason: "Approved skill improvement",
          },
        ],
      },
    };
  }
  for (const inventory of s.inventories) {
    const name =
      s.characters.find((c) => c.id === inventory.actor_id)?.name ??
      "Your character";
    inventory.items.push(
      {
        id: "sword-1",
        name: "Arming sword",
        description: "A serviceable steel blade.",
        quantity: 1,
        unit_weight_grams: 1200,
        location: "carried",
        container_id: null,
        allowed_actions: ["inspect"],
      },
      {
        id: "satchel-1",
        name: "Leather satchel",
        description: "A buckled bag with one main compartment.",
        quantity: 1,
        unit_weight_grams: 500,
        location: "carried",
        container_id: null,
        allowed_actions: ["inspect"],
      },
      {
        id: "coat-1",
        name: "Travel coat",
        description: "A heavy wool coat.",
        quantity: 1,
        unit_weight_grams: 2500,
        location: "equipped",
        container_id: null,
        allowed_actions: ["inspect"],
      },
    );
    inventory.encumbrance = "light";
    inventory.total_weight_grams = inventory.version === "i2" ? 4250 : 4300;
    s.inventoryDetails[inventory.actor_id] = {
      version: inventory.version,
      currency: [
        { label: "Silver crowns", minorAmount: 1250, fractionDigits: 2 },
      ],
      containers: [
        {
          id: "satchel-1",
          label: "Leather satchel",
          capacityLabel: "0 / 2,000 g stored",
          accessible: true,
        },
        {
          id: "sealed-chest",
          label: "Sealed chest",
          capacityLabel: "Inaccessible",
          accessible: false,
        },
      ],
      recipients: [
        { id: "guide-1", label: "Sera · beside you", reachable: true },
        {
          id: "distant-ally",
          label: "Known ally · not nearby",
          reachable: false,
        },
      ],
      slots: [
        { id: "main-hand", label: "Main hand" },
        { id: "body", label: "Body" },
      ],
      items: Object.fromEntries(
        inventory.items.map((item) => [
          item.id,
          {
            owner: name,
            custody: `Carried by ${name}`,
            affordances: affordances(item.id === "bandage-1"),
          },
        ]),
      ),
    };
  }
  return s;
}
