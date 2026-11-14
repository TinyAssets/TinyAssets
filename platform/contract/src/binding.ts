/**
 * The binding every owner door takes (ADR-032): who the object's owner is, who asks and
 * through what, as the binder (`doors/src/binder.ts`) verified it from a credential, never a
 * request field; and what the inbound door answers the binder. The doors make it and the
 * owner's object reads it (`owner/src/owner-do.ts`), so it is the contract's.
 */
import type { Vendor } from "./apps";
import { sha256Hex } from "./digest";
import { HEX16, HEX32, HEX64, RUN_ID } from "./ids";
import { Refusal } from "./refusal";
import { principal } from "./text";

/**
 * The reserved caller only the platform's admin path sends (the binder's `bindPlatformFor`, the
 * sign-up's explicit admit): `admissionSet`'s alone. The owner's object takes it at that
 * door and refuses it at every other door and as an owner (`owner/src/admission.ts`); a
 * guard keeps the name out of every source but this constant
 * (`owner/guards/admission-caller.test.ts`).
 */
export const PLATFORM_CALLER = "platform";

/** A verified outside client: what the binder resolved the caller's credential to. */
export interface Origin {
  readonly client: string;
  readonly family: string;
}

/** How the binder verified an origin: an API key (`ref` is its key id) or an MCP client. */
export interface Source {
  readonly kind: "api_key" | "mcp";
  readonly ref: string;
}

/**
 * What the binder knows of the sandbox from its token (`Binding.sandbox`): which token it
 * is, the SHA-256 of its secret, and for the egress token the egress generation E2B's
 * transform set beside it.
 */
export type SandboxToken =
  | { readonly kind: "egress"; readonly token: string; readonly generation: number;
      /** The SHA-256 of the run's own secret its path carried (`/r/<run>/<secret>/...`). */
      readonly secret?: string;
      /** The SHA-256 of the runner's secret (`x-ta-runner`): the runner's own doors. */
      readonly runner?: string }
  /**
   * The sync agent's call: its sync token's hash, and the egress token's E2B's transform
   * set beside it (slice S review M-5): the sync token alone, taken out of the VM, reaches
   * nothing, since only a call from inside the sandbox carries its own lease's egress token.
   */
  | { readonly kind: "sync"; readonly token: string; readonly egress: string };

/**
 * Whose object this is, who is asking and through what: set by the binder from a
 * verified credential, the one binding every door takes. `caller` is the owner's own
 * account, or, at the collection doors alone, another owner visiting (origin null).
 * `origin` is the verified outside client the owner's account acts through (an outside
 * agent), or null. The object takes every sender and reader from here, never from a
 * request field.
 */
export interface Binding {
  readonly owner: string;
  readonly caller: string;
  readonly origin: Origin | null;
  /** How the binder verified `origin`: an MCP client or an API key. Every outside call. */
  readonly source?: Source;
  /**
   * When the credential was signed in, in whole seconds: an outside client's, which a
   * revoke fences, or the owner's interactive sign-in, which opens a session. Every MCP
   * call carries it: the grant's original interactive sign-in, which a token refresh never
   * moves. A revoke, and once any MCP revoke is forgotten the owner-wide fence, refuses an
   * MCP call without it.
   */
  readonly authenticated_at?: number;
  /** The owner's protected-session cookie, as the browser presented it. */
  readonly session?: string;
  /**
   * The sandbox, as the egress door read its token (`owner/src/egress.ts`): which token it is (the
   * egress token or the sync token), the SHA-256 of its secret and, for the egress token,
   * the egress generation its
   * network's transform set. Only the egress doors take it; every other door refuses it.
   */
  readonly sandbox?: SandboxToken;
  /**
   * A run's call on the egress host (its MCP door, its event door): the run its path named,
   * beside the egress token's `sandbox`. The object resolves the live lease, the run under
   * it and its grant, and holds every door to that grant (`#runAccount`).
   */
  readonly run?: string;
  /**
   * The tier numbers of the platform's control record (`tier.ts`), as the binder read them
   * for a run door: never a request field, and never the owner's tier, which is the
   * object's own row.
   */
  readonly tiers?: unknown;
  /**
   * The served records whose vendor sign-in the platform's control record switches on, as
   * the binder read it for this call (`signins.ts`, `switches`): never a request field. The
   * object keeps the last list it was handed for the runs its own lane starts.
   */
  readonly signins?: unknown;
  /**
   * The first-party grant the owner's token was issued under, and that token's expiry
   * (whole seconds), from the provider's own unwrap: a socket ticket's binding, and a
   * sign-out's (`bindOwnerGrant`, `bindSignOut`).
   */
  readonly grant?: string;
  readonly expires_at?: number;
  /**
   * An inbound event's route, as the binder read it from the directory (`h:` or `i:`): the
   * owner's binding it names, at the revision the entry carries. Only `inboundAccept` takes
   * it; every other door refuses it.
   */
  readonly inbound?: { readonly binding: string; readonly revision: number };
  /**
   * The cutover's import (`doors/src/import/`): the SHA-256 of the bundle whose message the
   * doors verified under their `IMPORT_KEY`, beside the platform's reserved caller. Only the
   * import doors take it (`owner/src/import.ts`), and they take nothing else; deleted at the
   * cutover with them.
   */
  readonly import?: { readonly bundle: string };
  /**
   * Which of `apps.json`'s vendors an MCP client is, as its sign-in knew it (the grant's
   * props, `apps.ts`): recorded on its grant's row, never a request field.
   */
  readonly vendor?: Vendor | null;
}

// -- The kinds ----------------------------------------------------------------------------
//
// A binding is one of these kinds, each the fields of `Binding` it carries, and each with its
// one parser: the binder builds a kind, and the object parses a door's binding with the
// parser of the kind its row's caller takes (`BINDING_PARSERS`), so a field the binder sends
// and the parser lacks is a type error. A parser refuses anything else as `invalid`. Two doors
// take no binding (`UNBOUND_DOORS`, `doors.ts`).

/** The tier numbers and switched-on sign-ins a door's binding carries, as the binder read them. */
type Gated = Pick<Binding, "tiers" | "signins">;

/**
 * The owner's own account, acting itself or through a verified outside client (an MCP
 * client or an API key), or another owner visiting; its first-party token's grant, a fresh
 * sign-in's time, and the browser's session cookie where one rode the request.
 */
export type AccountBinding = Pick<Binding, "owner" | "caller" | "origin" | "source"
  | "authenticated_at" | "session" | "grant" | "expires_at" | "vendor"> & Gated;
/** The owner in its live session: an account binding with its session cookie. */
export type SessionBinding = AccountBinding & { readonly session: string };
/** A sandbox's token at its own doors (the egress door's, the runner's, the sync agent's). */
export type SandboxBinding = Pick<Binding, "owner" | "caller"> & {
  readonly origin: null; readonly sandbox: SandboxToken };
/** A run's call on the egress host: the egress token, and the run its path names. */
export type RunBinding = Pick<Binding, "owner" | "caller"> & {
  readonly origin: null; readonly sandbox: Extract<SandboxToken, { kind: "egress" }>;
  readonly run: string };
/** An inbound event's route, at its binding's revision. */
export type InboundBinding = Pick<Binding, "owner" | "caller"> & Gated & {
  readonly origin: null; readonly inbound: NonNullable<Binding["inbound"]> };
/**
 * The platform's reserved caller for an owner, with no outside client and no session: its
 * admit and tier doors, and an erasure's doors (the same binding, while the owner is marked).
 */
export type ReservedBinding = Pick<Binding, "owner"> & {
  readonly caller: typeof PLATFORM_CALLER; readonly origin: null };
/** The cutover's import: the reserved caller with the verified bundle and the tier numbers. */
export type ImportBinding = ReservedBinding & {
  readonly import: NonNullable<Binding["import"]>; readonly tiers: unknown };

function invalid(kind: string): never {
  throw new Refusal("invalid", `invalid ${kind} binding`);
}

/** `value` as a plain object whose keys are all `required` and some of `optional`. */
function fields(value: unknown, kind: string, required: readonly string[],
                optional: readonly string[] = []): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return invalid(kind);
  }
  const keys = Object.keys(value);
  if (required.some((key) => !keys.includes(key))
      || keys.some((key) => !required.includes(key) && !optional.includes(key))) {
    return invalid(kind);
  }
  return value as Record<string, unknown>;
}

function hex(value: unknown, grammar: RegExp, kind: string): string {
  return typeof value === "string" && grammar.test(value) ? value : invalid(kind);
}

/** A whole number from `min`: seconds, a generation, a revision. */
function whole(value: unknown, min: number, kind: string): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min ? value
    : invalid(kind);
}

function text(value: unknown, kind: string): string {
  return typeof value === "string" && value !== "" ? value : invalid(kind);
}

/** A verified outside client: exactly a 64-hex client and family. */
function origin(value: unknown, kind: string): Origin {
  const { client, family } = fields(value, kind, ["client", "family"]);
  return Object.freeze({ client: hex(client, HEX64, kind), family: hex(family, HEX64, kind) });
}

/** An API key's id, or an MCP client's OAuth `client_id`. */
function source(value: unknown, kind: string): Source {
  const { kind: by, ref } = fields(value, kind, ["kind", "ref"]);
  if (by === "api_key") {
    return Object.freeze({ kind: by, ref: hex(ref, HEX16, kind) });
  }
  return by === "mcp" && typeof ref === "string" && ref !== "" && ref.length <= 512
    ? Object.freeze({ kind: by, ref }) : invalid(kind);
}

function vendor(value: unknown, kind: string): Vendor | null {
  if (value === null) {
    return null;
  }
  const { id, verified } = fields(value, kind, ["id", "verified"]);
  return typeof id === "string" && typeof verified === "boolean"
    ? Object.freeze({ id, verified }) : invalid(kind);
}

/** The owner's own principal, as its caller too, with no outside client. */
function own(given: Record<string, unknown>, kind: string): string {
  const owner = principal(given["owner"]);
  return principal(given["caller"]) === owner && given["origin"] === null ? owner
    : invalid(kind);
}

/** The platform's reserved caller for one owner, with no outside client. */
function platform(given: Record<string, unknown>, kind: string): string {
  return given["caller"] === PLATFORM_CALLER && given["origin"] === null
    ? principal(given["owner"]) : invalid(kind);
}

/** An account binding (`AccountBinding`), or `invalid`. */
export function accountBinding(value: unknown): AccountBinding {
  const kind = "account";
  const given = fields(value, kind, ["owner", "caller", "origin"],
                       ["source", "authenticated_at", "session", "grant", "expires_at", "vendor",
                        "tiers", "signins"]);
  const has = (key: string) => key in given;
  return Object.freeze({
    owner: principal(given["owner"]), caller: principal(given["caller"]),
    origin: given["origin"] === null ? null : origin(given["origin"], kind),
    ...(has("source") ? { source: source(given["source"], kind) } : {}),
    ...(has("authenticated_at")
      ? { authenticated_at: whole(given["authenticated_at"], 0, kind) } : {}),
    ...(has("session") ? { session: text(given["session"], kind) } : {}),
    ...(has("grant") ? { grant: text(given["grant"], kind) } : {}),
    ...(has("expires_at") ? { expires_at: whole(given["expires_at"], 0, kind) } : {}),
    ...(has("vendor") ? { vendor: vendor(given["vendor"], kind) } : {}),
    ...(has("tiers") ? { tiers: given["tiers"] } : {}),
    ...(has("signins") ? { signins: given["signins"] } : {}),
  });
}

/** A session binding (`SessionBinding`): an account binding with its session, or `invalid`. */
export function sessionBinding(value: unknown): SessionBinding {
  const account = accountBinding(value);
  return account.session === undefined ? invalid("session")
    : { ...account, session: account.session };
}

/**
 * A sandbox's token, exactly (`Binding.sandbox`): the egress token, with a run's or the
 * runner's secret or neither, or the sync token; or `invalid`.
 */
export function sandboxToken(value: unknown): SandboxToken {
  const kind = "sandbox";
  if (typeof value === "object" && value !== null && "egress" in value) {
    const given = fields(value, kind, ["kind", "token", "egress"]);
    return given["kind"] === "sync"
      ? Object.freeze({ kind: "sync", token: hex(given["token"], HEX64, kind),
                        egress: hex(given["egress"], HEX64, kind) })
      : invalid(kind);
  }
  const given = fields(value, kind, ["kind", "token", "generation"], ["secret", "runner"]);
  if (given["kind"] !== "egress" || ("secret" in given && "runner" in given)) {
    return invalid(kind);
  }
  const token = hex(given["token"], HEX64, kind);
  const generation = whole(given["generation"], 1, kind);
  return Object.freeze("secret" in given
    ? { kind: "egress", token, generation, secret: hex(given["secret"], HEX64, kind) }
    : "runner" in given
      ? { kind: "egress", token, generation, runner: hex(given["runner"], HEX64, kind) }
      : { kind: "egress", token, generation });
}

/** A sandbox binding (`SandboxBinding`), or `invalid`. */
export function sandboxBinding(value: unknown): SandboxBinding {
  const kind = "sandbox";
  const given = fields(value, kind, ["owner", "caller", "origin", "sandbox"]);
  const owner = own(given, kind);
  return Object.freeze({ owner, caller: owner, origin: null,
                         sandbox: sandboxToken(given["sandbox"]) });
}

/** A run binding (`RunBinding`), or `invalid`. */
export function runBinding(value: unknown): RunBinding {
  const kind = "run";
  const given = fields(value, kind, ["owner", "caller", "origin", "sandbox", "run"]);
  const owner = own(given, kind);
  const sandbox = sandboxToken(given["sandbox"]);
  return sandbox.kind === "egress"
    ? Object.freeze({ owner, caller: owner, origin: null, sandbox,
                      run: hex(given["run"], RUN_ID, kind) })
    : invalid(kind);
}

/** An inbound binding (`InboundBinding`), or `invalid`. */
export function inboundBinding(value: unknown): InboundBinding {
  const kind = "inbound";
  const given = fields(value, kind, ["owner", "caller", "origin", "inbound"],
                       ["tiers", "signins"]);
  const owner = own(given, kind);
  const { binding, revision } = fields(given["inbound"], kind, ["binding", "revision"]);
  return Object.freeze({
    owner, caller: owner, origin: null,
    inbound: Object.freeze({ binding: hex(binding, HEX32, kind),
                             revision: whole(revision, 1, kind) }),
    ...("tiers" in given ? { tiers: given["tiers"] } : {}),
    ...("signins" in given ? { signins: given["signins"] } : {}),
  });
}

/** A reserved binding (`ReservedBinding`), an erasure's too, or `invalid`. */
export function reservedBinding(value: unknown): ReservedBinding {
  const kind = "reserved";
  const owner = platform(fields(value, kind, ["owner", "caller", "origin"]), kind);
  return Object.freeze({ owner, caller: PLATFORM_CALLER, origin: null });
}

/** An import binding (`ImportBinding`), or `invalid`. */
export function importBinding(value: unknown): ImportBinding {
  const kind = "import";
  const given = fields(value, kind, ["owner", "caller", "origin", "import", "tiers"]);
  const owner = platform(given, kind);
  const { bundle } = fields(given["import"], kind, ["bundle"]);
  return Object.freeze({ owner, caller: PLATFORM_CALLER, origin: null,
                         import: Object.freeze({ bundle: hex(bundle, HEX64, kind) }),
                         tiers: given["tiers"] });
}

/**
 * Each binding kind's parser, by the kind: the object parses a door's binding with the one
 * its row's caller takes. An erasure's doors take the reserved binding, as the admit does.
 */
export const BINDING_PARSERS = Object.freeze({
  account: accountBinding,
  session: sessionBinding,
  sandbox: sandboxBinding,
  run: runBinding,
  inbound: inboundBinding,
  reserved: reservedBinding,
  erasure: reservedBinding,
  import: importBinding,
});
export type BindingKind = keyof typeof BINDING_PARSERS;

/** What the inbound door answers the binder for one event (`inboundAccept`). */
export type InboundAnswer =
  | { readonly state: "admitted"; readonly receipt: string; readonly deduped: boolean }
  | { readonly state: "refused"; readonly error: string; readonly retry?: true }
  | { readonly state: "no_op"; readonly error: string };

/**
 * The outside identity an API key resolves to, by its key id alone (the Python's
 * `api_keys._client` and `_family`, byte for byte): the binder binds a key's call to it, and
 * the object re-derives it wherever it matters (`registrant`).
 */
export function apiKeyOrigin(keyId: string): Origin {
  return {
    client: sha256Hex("tinyassets-api-key-client\0" + keyId),
    family: sha256Hex("tinyassets-api-key-family\0" + keyId),
  };
}

/** The ledger id of the owner's agent `agent`: `ta_` and its name's digest, 16 hex. */
export function nativeId(agent: string): string {
  return "ta_" + sha256Hex(`tinyassets-native-agent\0${agent}`).slice(0, 16);
}
