import { expect, it } from "vitest";

import { bundledSources } from "../../sources/src/catalog";
import { statesHold, statusMatches } from "./login";

it("checks the named fields in the sign-in status document", () => {
  const equals = { "account.signed": true, method: "member" };
  expect(statusMatches('{"account":{"signed":true},"method":"member"}', equals)).toBe(true);
  expect(statusMatches('{"account.signed":true,"method":"member"}', equals)).toBe(false);
  expect(statusMatches('{"account":{"signed":false},"method":"member"}', equals)).toBe(false);
  expect(statusMatches("unreadable", equals)).toBe(false);
});

it("reads sign-in evidence from the JSON fields in a stored file", () => {
  const codex = bundledSources().get("codex")!.cli!.login!;
  expect(statesHold(codex, () => '{"tokens":{"access_token":"a","refresh_token":"r"}}')).toBe(true);
  expect(statesHold(codex, () => '{"tokens.access_token":"a","tokens.refresh_token":"r"}')).toBe(false);
  const login = { ...codex, state_checks: [{ path: "{home}/stored.json", present: ["entries.length"] }] };
  expect(statesHold(login, () => '{"entries":{"length":2}}')).toBe(true);
  expect(statesHold(login, () => '{"entries":["a","b"]}')).toBe(false);
});
