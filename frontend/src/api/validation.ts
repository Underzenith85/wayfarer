import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import shared from "../../../contracts/v1/schemas.json";
import events from "../../../contracts/v1/events.schema.json";
import type { components } from "./contracts.generated";
import type { ClientMessage, ServerMessage } from "./events.generated";
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(shared);
ajv.addSchema(events);
export function parseSchema<T>(reference: string, value: unknown): T {
  const validate = ajv.getSchema(reference);
  if (!validate || !validate(value))
    throw new Error(
      `Contract violation: ${reference}: ${JSON.stringify(validate?.errors)}`,
    );
  return value as T;
}
export function parseHttp<K extends keyof components["schemas"]>(
  name: K,
  value: unknown,
): components["schemas"][K] {
  return parseSchema(`${shared.$id}#/$defs/${name}`, value);
}
export const parseClientMessage = (value: unknown) =>
  parseSchema<ClientMessage>(`${events.$id}#/$defs/ClientMessage`, value);
export const parseServerMessage = (value: unknown) =>
  parseSchema<ServerMessage>(`${events.$id}#/$defs/ServerMessage`, value);
