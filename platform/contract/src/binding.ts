/**
 * The binding every owner door takes (ADR-032): who the object's owner is, who asks and
 * through what, as the binder (`doors/src/binder.ts`) verified it from a credential, never a
 * request field; and what the inbound door answers the binder. The doors make it and the
 * owner's object reads it (`owner/src/owner-do.ts`), so it is the contract's.
 */
import type { Vendor } from "./apps";
import { sha256Hex } from "./digest";

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
export type SandboxBinding =
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
  readonly sandbox?: SandboxBinding;
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
   * Which of `apps.json`'s vendors an MCP client is, as its sign-in knew it (the grant's
   * props, `apps.ts`): recorded on its grant's row, never a request field.
   */
  readonly vendor?: Vendor | null;
}

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
