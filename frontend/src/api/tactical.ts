import createClient from "openapi-fetch";
import { Ajv2020 } from "ajv/dist/2020";
import addFormats from "ajv-formats";
import document from "../../../contracts/tactical/v1/openapi.json";
import type { paths, components } from "./tactical.generated";

export type TacticalSnapshot = components["schemas"]["TacticalSnapshot"];
export type TacticalCommand =
  components["schemas"]["TacticalRequest"]["command"];
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
const validate = ajv.compile<TacticalSnapshot>({
  $ref: "#/components/schemas/TacticalSnapshot",
  components: document.components,
});
export function parseTactical(value: unknown): TacticalSnapshot {
  if (!validate(value))
    throw new Error("Invalid tactical response; reconnect before acting.");
  return value;
}
export class TacticalError extends Error {}
export class TacticalClient {
  private client;
  constructor(origin: string, credential: string) {
    this.client = createClient<paths>({
      baseUrl: `${origin}/api/tactical/v1`,
      headers: { Authorization: `Bearer ${credential}` },
    });
  }
  async read(cid: string, actor: string, signal: AbortSignal) {
    const result = await this.client.GET("/campaigns/{cid}", {
      params: { path: { cid }, query: { actor_id: actor } },
      signal,
    });
    if (result.error) throw new TacticalError(result.error.error);
    return parseTactical(result.data);
  }
  async execute(cid: string, command: TacticalCommand, signal: AbortSignal) {
    const result = await this.client.POST("/campaigns/{cid}/commands", {
      params: { path: { cid } },
      body: { command },
      signal,
    });
    if (result.error) throw new TacticalError(result.error.error);
    return parseTactical(result.data);
  }
}
