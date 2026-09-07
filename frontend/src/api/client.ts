import createClient from "openapi-fetch";
import type { paths, components } from "./contracts.generated";
import { operations } from "./operations.generated";
import { parseHttp } from "./validation";
/** Generated paths constrain every method, path parameter, body and result. */
export function createApiClient(
  baseUrl: string,
  credential: string,
  fetcher: typeof fetch = fetch,
) {
  const client = createClient<paths>({
    baseUrl,
    fetch: fetcher,
    headers: { Authorization: `Bearer ${credential}` },
  });
  client.use({
    async onRequest({ request, schemaPath }) {
      const operation = Object.values(operations).find(
        (x) => x.path === schemaPath && x.method === request.method,
      );
      if (operation?.requestSchema)
        parseHttp(operation.requestSchema, await request.clone().json());
    },
    async onResponse({ request, response, schemaPath }) {
      const operation = Object.values(operations).find(
        (x) => x.path === schemaPath && x.method === request.method,
      );
      const responses: Record<string, keyof components["schemas"]> =
        operation?.responses ?? {};
      const schema = responses[String(response.status)];
      if (!schema)
        throw new Error(`Undocumented response status ${response.status}`);
      parseHttp(schema, await response.clone().json());
      return response;
    },
  });
  return client;
}
