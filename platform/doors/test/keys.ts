/** An owner signed in to its app with one minted key, for tests that need a live key. */
import { appPost, signInApp } from "./support";

export async function mintKeyFor(owner: string):
    Promise<{ key: string; key_id: string; token: string; session: string }> {
  const app = await signInApp({ owner });
  const response = await appPost("keys", app.access_token, app.session, {
    name: "test", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
  if (response.status !== 200) {
    throw new Error(`mint: ${response.status} ${await response.text()}`);
  }
  const { key, key_id } = await response.json<{ key: string; key_id: string }>();
  return { key, key_id, token: app.access_token, session: app.session };
}
