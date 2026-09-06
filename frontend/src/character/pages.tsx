import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "@tanstack/react-router";
import { Backpack, Heart, Shield, Footprints, Coins } from "lucide-react";
import { Button } from "../components/ui/button";
import { Sheet } from "../components/ui/sheet";
import { usePlay } from "../play/use-play";
import type { components } from "../api/contracts.generated";
import {
  operationLabel,
  type InventoryIntent,
  type InventoryOperation,
} from "./presentation";
type Character = components["schemas"]["Character"];
type Stat = components["schemas"]["Stat"];
function EmptyCharacter() {
  const { state } = usePlay();
  return (
    <section className="scene-card">
      <h2>
        {state.expired
          ? "Session ended"
          : state.loading
            ? "Loading character…"
            : "No character selected"}
      </h2>
      <p>
        {state.expired
          ? "Private character information has been cleared. Reconnect to continue."
          : "Choose a campaign and a controlled character to view their sheet and inventory."}
      </p>
      {!state.expired && (
        <Button asChild>
          <Link to="/campaign">Choose a campaign</Link>
        </Button>
      )}
    </section>
  );
}
function ActorPicker() {
  const { store, state } = usePlay();
  const s = state.snapshot;
  if (!s) return null;
  return (
    <label className="actor-select">
      Character
      <select
        value={state.actorId ?? ""}
        disabled={state.busy || !!state.retry}
        onChange={(e) => store.chooseActor(e.target.value)}
      >
        {!state.actorId && <option value="">Select a character</option>}
        {s.characters
          .filter(
            (c) =>
              s.campaign.membership.actor_ids.includes(c.id) &&
              s.scene.visible_actor_ids.includes(c.id),
          )
          .map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
      </select>
    </label>
  );
}
function StatTable({ title, stats }: { title: string; stats: Stat[] }) {
  return (
    <section className="sheet-section">
      <h3>{title}</h3>
      {stats.length ? (
        <dl className="sheet-stats">
          {stats.map((stat) => (
            <div key={stat.id}>
              <dt>{stat.label}</dt>
              <dd>{stat.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p>No {title.toLowerCase()} reported.</p>
      )}
    </section>
  );
}
function ResourcePool({
  label,
  pool,
}: {
  label: string;
  pool: Character["hp"];
}) {
  return (
    <div className="resource-pool">
      <span>{label}</span>
      <strong>
        {pool.current} <small>/ {pool.maximum}</small>
      </strong>
      <meter
        aria-label={label}
        min={Math.min(0, pool.current)}
        max={pool.maximum}
        value={Math.min(pool.current, pool.maximum)}
      />
    </div>
  );
}
export function CharacterPage() {
  const { state } = usePlay();
  const s = state.snapshot;
  const c = s?.characters.find((c) => c.id === state.actorId);
  if (!s || !c) return <EmptyCharacter />;
  const extra = s.characterDetails?.[c.id];
  return (
    <div className="character-sheet">
      <ActorPicker />
      <header className="character-banner">
        <span className="eyebrow">Character sheet</span>
        <h2>{c.name}</h2>
        <p>{extra?.rulesLabel ?? "Authoritative character status"}</p>
        <span className="resource-version">Version {c.version}</span>
      </header>
      <div className="pool-grid">
        <ResourcePool label="Hit points" pool={c.hp} />
        <ResourcePool label="Fatigue points" pool={c.fp} />
      </div>
      <section className="sheet-section">
        <h3>
          <Heart size={18} aria-hidden="true" />
          Conditions
        </h3>
        {c.conditions.length ? (
          <ul className="condition-list">
            {c.conditions.map((condition) => (
              <li key={condition.id}>
                <strong>{condition.label}</strong>
                <p>{condition.description}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p>No active conditions reported.</p>
        )}
      </section>
      <div className="character-columns">
        <StatTable title="Attributes" stats={c.attributes} />
        <StatTable title="Skills" stats={c.skills} />
      </div>
      <div className="character-columns">
        <StatTable title="Defenses" stats={c.defenses} />
        <StatTable title="Movement" stats={c.movement} />
      </div>
      <section className="sheet-section">
        <h3>
          <Footprints size={18} aria-hidden="true" />
          Derived effects
        </h3>
        {extra?.effects.length ? (
          <ul className="condition-list">
            {extra.effects.map((effect) => (
              <li key={effect.id}>
                <strong>{effect.label}</strong>
                <p>{effect.description}</p>
                <small>Source: {effect.source}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p>
            {extra
              ? "No additional effects reported."
              : "Derived-effect details are not available from this connection."}
          </p>
        )}
      </section>
      <section className="sheet-section">
        <h3>
          <Shield size={18} aria-hidden="true" />
          Points & advancement
        </h3>
        {extra ? (
          <>
            <dl className="points-grid">
              <div>
                <dt>Build total</dt>
                <dd>{extra.points.buildTotal}</dd>
              </div>
              <div>
                <dt>Earned</dt>
                <dd>{extra.points.earned}</dd>
              </div>
              <div>
                <dt>Spent</dt>
                <dd>{extra.points.spent}</dd>
              </div>
              <div>
                <dt>Available</dt>
                <dd>{extra.points.available}</dd>
              </div>
            </dl>
            <details>
              <summary>Advancement ledger</summary>
              <ul>
                {extra.points.ledger.map((entry) => (
                  <li key={entry.id}>
                    {entry.amount > 0 ? "+" : ""}
                    {entry.amount} · {entry.reason}
                  </li>
                ))}
              </ul>
              <p className="resource-version">
                Ledger version {extra.points.version}
              </p>
            </details>
            <p>
              Purchases require server validation. This sheet does not grant or
              spend points.
            </p>
          </>
        ) : (
          <p>
            Point and advancement information is not available from this
            connection.
          </p>
        )}
      </section>
    </div>
  );
}
export function InventoryFeedback() {
  const { state, store } = usePlay();
  const entry = state.entries.at(-1),
    action = entry?.action;
  return (
    <>
      <div className="inventory-feedback" aria-live="polite">
        {entry && (
          <>
            <p className="eyebrow">Latest action · {entry.text}</p>
            <p>
              {action?.status === "succeeded"
                ? "Committed"
                : action?.status === "rejected"
                  ? "Rejected — inventory unchanged"
                  : action?.status === "resolving"
                    ? "Resolving — inventory unchanged"
                    : action?.status === "submitted"
                      ? "Submitted — inventory unchanged"
                      : action?.status === "needs_clarification"
                        ? "Clarification needed in Play"
                        : action?.status === "cancelled"
                          ? "Cancelled"
                          : "Awaiting acknowledgement — inventory unchanged"}
            </p>
            {action?.status === "succeeded" && (
              <p>{action.resolution.summary}</p>
            )}
            {action?.status === "rejected" && (
              <p role="alert">{action.error.message}</p>
            )}
            {action?.status === "needs_clarification" && (
              <Button asChild>
                <Link to="/">Answer in Play</Link>
              </Button>
            )}
          </>
        )}
      </div>
      {state.error && (
        <div className="request-error" role="alert">
          <p>{state.error}</p>
          {state.retry ? (
            <Button disabled={state.busy} onClick={() => void store.retry()}>
              Retry same request
            </Button>
          ) : (
            state.selectedId && (
              <Button
                disabled={state.busy}
                onClick={() => void store.select(state.selectedId!)}
              >
                Review changed inventory
              </Button>
            )
          )}
        </div>
      )}
    </>
  );
}
function ItemOperations({ itemId }: { itemId: string }) {
  const { state, store } = usePlay();
  const s = state.snapshot!,
    actorId = state.actorId!;
  const inventory = s.inventories.find((i) => i.actor_id === actorId)!;
  const item = inventory.items.find((i) => i.id === itemId)!;
  const details = s.inventoryDetails?.[actorId],
    meta = details?.items[itemId];
  const [kind, setKind] = useState<InventoryOperation>("inspect");
  const [quantity, setQuantity] = useState("1");
  const [target, setTarget] = useState("");
  const reason = store.inventoryBlockReason(itemId, kind);
  const count = Number(quantity);
  const needsQuantity = ["use_item", "drop", "transfer", "store"].includes(
    kind,
  );
  const needsTarget = ["equip", "transfer", "store"].includes(kind);
  const targets =
    kind === "equip"
      ? (details?.slots.map((x) => ({ ...x, available: true })) ?? [])
      : kind === "store"
        ? (details?.containers.map((x) => ({
            ...x,
            available: x.accessible,
          })) ?? [])
        : (details?.recipients.map((x) => ({ ...x, available: x.reachable })) ??
          []);
  const selectedTarget = target || targets.find((x) => x.available)?.id || "";
  const validQuantity =
    !needsQuantity ||
    (Number.isSafeInteger(count) && count >= 1 && count <= item.quantity);
  const targetAllowed =
    !needsTarget || targets.some((x) => x.id === selectedTarget && x.available);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (reason || !validQuantity || !targetAllowed) return;
    let intent: InventoryIntent;
    switch (kind) {
      case "inspect":
        intent = { kind, target_id: itemId };
        break;
      case "use_item":
        intent = { kind, item_id: itemId, quantity: count };
        break;
      case "equip":
        intent = { kind, item_id: itemId, slot_id: selectedTarget };
        break;
      case "drop":
        intent = { kind, item_id: itemId, quantity: count };
        break;
      case "transfer":
        intent = {
          kind,
          item_id: itemId,
          quantity: count,
          recipient_actor_id: selectedTarget,
        };
        break;
      case "store":
        intent = {
          kind,
          item_id: itemId,
          quantity: count,
          container_id: selectedTarget,
        };
        break;
    }
    await store.sendInventory(intent);
  };
  return (
    <div className="item-detail">
      <p>{item.description}</p>
      <dl className="item-facts">
        <div>
          <dt>Quantity</dt>
          <dd>{item.quantity}</dd>
        </div>
        <div>
          <dt>Unit weight</dt>
          <dd>{item.unit_weight_grams} g</dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>{item.location}</dd>
        </div>
        <div>
          <dt>Owner</dt>
          <dd>{meta?.owner ?? "Not provided"}</dd>
        </div>
        <div>
          <dt>Custody</dt>
          <dd>{meta?.custody ?? "Not provided"}</dd>
        </div>
        <div>
          <dt>Container</dt>
          <dd>
            {item.container_id
              ? (details?.containers.find((c) => c.id === item.container_id)
                  ?.label ?? "Unknown container")
              : "None reported"}
          </dd>
        </div>
      </dl>
      <form className="item-operation-form" onSubmit={(e) => void submit(e)}>
        <fieldset disabled={state.busy || !!state.retry}>
          <legend>Item operation</legend>
          <label>
            Operation
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as InventoryOperation);
                setTarget("");
              }}
            >
              {Object.entries(operationLabel).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {needsQuantity && (
            <label>
              Quantity
              <input
                type="number"
                min={1}
                max={item.quantity}
                step={1}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                required
              />
            </label>
          )}
          {needsTarget && (
            <label>
              {kind === "equip"
                ? "Equipment slot"
                : kind === "store"
                  ? "Destination container"
                  : "Recipient"}
              <select
                value={selectedTarget}
                onChange={(e) => setTarget(e.target.value)}
                required
              >
                {!selectedTarget && (
                  <option value="">No permitted destination</option>
                )}
                {targets.map((t) => (
                  <option key={t.id} value={t.id} disabled={!t.available}>
                    {t.label}
                    {!t.available ? " · unavailable" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </fieldset>
        {reason && (
          <p className="operation-reason" role="status">
            {reason}
          </p>
        )}
        {!validQuantity && (
          <p role="alert">
            Choose a whole quantity between 1 and {item.quantity}.
          </p>
        )}
        {kind === "drop" && (
          <p>
            Dropped items leave your inventory. Confirm only if you intend to
            leave them behind.
          </p>
        )}
        {kind === "transfer" && (
          <p>Confirm the recipient before transferring custody.</p>
        )}
        <Button disabled={!!reason || !validQuantity || !targetAllowed}>
          Confirm {operationLabel[kind].toLowerCase()}
        </Button>
        <p className="resource-version">
          Uses inventory version {inventory.version}. The server validates every
          operation.
        </p>
      </form>
      <InventoryFeedback />
    </div>
  );
}
export function InventoryPage() {
  const { state, store } = usePlay();
  const s = state.snapshot;
  const inventory = s?.inventories.find((i) => i.actor_id === state.actorId);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const previousItems = useRef<string[]>([]);
  useEffect(() => {
    const current = inventory?.items.map((i) => i.id) ?? [];
    if (previousItems.current.some((id) => !current.includes(id)))
      document.getElementById("inventory-title")?.focus();
    previousItems.current = current;
  }, [inventory]);
  if (!s || !inventory || !state.actorId) return <EmptyCharacter />;
  const details = s.inventoryDetails?.[state.actorId];
  const items = inventory.items.filter(
    (item) =>
      (filter === "all" || item.location === filter) &&
      `${item.name} ${item.description}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <div className="inventory-page">
      <ActorPicker />
      <header className="character-banner">
        <span className="eyebrow">Equipment & belongings</span>
        <h2 id="inventory-title" tabIndex={-1}>
          <Backpack size={26} aria-hidden="true" />
          Inventory
        </h2>
        <div className="inventory-totals">
          <span>{inventory.total_weight_grams} g carried</span>
          <strong>{inventory.encumbrance} encumbrance</strong>
          <span className="resource-version">Version {inventory.version}</span>
        </div>
        <Button
          variant="outline"
          disabled={state.busy || !!state.retry}
          onClick={() => void store.select(s.campaign.id)}
        >
          Refresh inventory
        </Button>
      </header>
      <InventoryFeedback />
      {details && (
        <section className="currency-strip" aria-label="Currency">
          <Coins size={18} aria-hidden="true" />
          {details.currency.length ? (
            details.currency.map((c) => (
              <span key={c.label}>
                {c.label}:{" "}
                {(c.minorAmount / 10 ** c.fractionDigits).toFixed(
                  c.fractionDigits,
                )}
              </span>
            ))
          ) : (
            <span>No currency reported.</span>
          )}
        </section>
      )}
      {!details && (
        <p>
          Currency and custody details are not available from this connection.
        </p>
      )}
      <div className="inventory-filters">
        <label>
          Find an item
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name or description"
          />
        </label>
        <label>
          Location
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {["all", "carried", "equipped", "stored", "confiscated"].map(
              (location) => (
                <option key={location} value={location}>
                  {location === "all"
                    ? "All locations"
                    : location[0]!.toUpperCase() + location.slice(1)}
                </option>
              ),
            )}
          </select>
        </label>
      </div>
      <ul className="item-list">
        {items.map((item) => (
          <li key={item.id} className="item-card">
            <div>
              <span className="eyebrow">{item.location}</span>
              <h3>{item.name}</h3>
              <p>{item.description}</p>
              <p className="item-meta">
                Quantity {item.quantity} · {item.unit_weight_grams} g each
                {item.container_id
                  ? ` · ${details?.containers.find((c) => c.id === item.container_id)?.label ?? "Unknown container"}`
                  : ""}
              </p>
              {item.location === "confiscated" && (
                <p className="custody-note">
                  {details?.items[item.id]?.custody ??
                    "Not in your custody. Location unknown."}
                </p>
              )}
            </div>
            <Sheet
              title={item.name}
              trigger={<Button variant="outline">View {item.name}</Button>}
            >
              <ItemOperations
                key={`${item.id}:${inventory.version}`}
                itemId={item.id}
              />
            </Sheet>
          </li>
        ))}
      </ul>
      {!items.length && (
        <p role="status">
          {inventory.items.length
            ? "No items match these filters."
            : "No items in this inventory."}
        </p>
      )}
      {details?.containers.length ? (
        <section className="sheet-section">
          <h3>Known containers</h3>
          <ul>
            {details.containers.map((c) => (
              <li key={c.id}>
                {c.label} · {c.capacityLabel}
                {!c.accessible ? " · unavailable" : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
