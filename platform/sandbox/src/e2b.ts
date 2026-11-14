/**
 * E2B's sandbox lifecycle, as the sandbox Worker runs it, over the one transport
 * (`../e2b/transport.ts`, which holds the platform API's one call, envd and the calls every
 * caller shares): create (`POST /v2/sandboxes`, `secure: true`, so envd takes only the
 * sandbox's own envd token, probed live), info (`GET /sandboxes/{id}`), pause
 * (`POST /sandboxes/{id}/pause`, `503` while a previous snapshot finishes: the sandbox keeps
 * running), connect, which resumes a paused sandbox and only extends its timeout
 * (`POST /v2/sandboxes/{id}/connect`), the team's running count and the list
 * (`GET /v2/sandboxes`). The tools' calls (a template's build and tags, a throwaway sandbox,
 * the team's webhooks) are `../e2b/admin.ts`'s, which the Worker never imports.
 */
import type { NetworkDocument } from "../../contract/src/sandbox";
import {
  created, E2bError, E2bTransport, type Handle, object, runtimeFetch, sandboxId,
} from "../e2b/transport";

/**
 * Whether an answer means E2B has no room for one more running sandbox now: its
 * concurrency limit (`429`) or no capacity to place one (`503`, or the
 * `sandbox_capacity_unavailable` code). A run then waits, never refused.
 */
export function noRoom(error: unknown): boolean {
  return error instanceof E2bError && (error.status === 429 || error.status === 503
    || error.code === "sandbox_capacity_unavailable");
}

/** What the platform API tells of one sandbox. */
export interface SandboxInfo {
  readonly sandboxID: string;
  readonly state: "running" | "paused";
  readonly endAt: string;
  /** What its last pause saved: `filesystem` reboots it on resume, memory lost. */
  readonly pauseMode: "full" | "filesystem" | null;
  readonly envdAccessToken: string | null;
  readonly metadata: Readonly<Record<string, string>>;
  readonly network: unknown;
}

function info(value: unknown): SandboxInfo {
  if (!object(value) || typeof value["sandboxID"] !== "string"
      || (value["state"] !== "running" && value["state"] !== "paused")
      || typeof value["endAt"] !== "string") {
    throw new E2bError(502, null, "E2B's sandbox info is out of shape");
  }
  const mode = value["pauseMode"];
  return {
    sandboxID: value["sandboxID"], state: value["state"], endAt: value["endAt"],
    pauseMode: mode === "full" || mode === "filesystem" ? mode : null,
    envdAccessToken: typeof value["envdAccessToken"] === "string"
      ? value["envdAccessToken"] : null,
    metadata: object(value["metadata"]) ? value["metadata"] as Record<string, string> : {},
    network: value["network"] ?? null,
  };
}

/** The Worker's `E2b`, on the runtime's own fetch. */
export function runtimeE2b(key: string): E2b {
  return new E2b(key, runtimeFetch);
}

/** The sandbox Worker's E2B: the transport's calls and a sandbox's lifecycle. */
export class E2b extends E2bTransport {
  /**
   * A new sandbox from `template`, its network and its timeout, paused on timeout with its
   * memory. `secure`: envd answers only a caller with the sandbox's envd token (without it,
   * anything that reaches envd could run commands as root, undoing the per-run users).
   */
  async create(request: { template: string; timeoutS: number; network: NetworkDocument;
                          metadata: Readonly<Record<string, string>> }): Promise<Handle> {
    const { json } = await this.api("POST", "/v2/sandboxes", {
      templateID: request.template, timeout: request.timeoutS, secure: true, autoPause: true,
      autoPauseMemory: true, autoResume: { enabled: false }, network: request.network,
      metadata: request.metadata,
    }, [201, 200]);
    return created(json);
  }

  /** The sandbox's info, or null when E2B has no such sandbox (killed). */
  async info(id: string): Promise<SandboxInfo | null> {
    try {
      return info((await this.api("GET", `/sandboxes/${sandboxId(id)}`)).json);
    } catch (error) {
      if (error instanceof E2bError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  /**
   * Pause it with its memory: `paused`, or `busy` when E2B refused because a previous
   * snapshot is still being written (the sandbox runs on, unharmed), or `gone`.
   */
  async pause(id: string): Promise<"paused" | "busy" | "gone"> {
    try {
      await this.api("POST", `/sandboxes/${sandboxId(id)}/pause`, { memory: true }, [204, 409]);
      return "paused";
    } catch (error) {
      if (error instanceof E2bError && error.status === 503) {
        return "busy";
      }
      if (error instanceof E2bError && error.status === 404) {
        return "gone";
      }
      throw error;
    }
  }

  /** Resume it if paused (memory restored), its timeout at least `timeoutS` from now. */
  async connect(id: string, timeoutS: number): Promise<Handle | null> {
    try {
      return created((await this.api("POST", `/v2/sandboxes/${sandboxId(id)}/connect`,
                                     { timeout: timeoutS }, [200, 201])).json);
    } catch (error) {
      if (error instanceof E2bError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  /**
   * How many sandboxes E2B runs now for the whole team the key is of (`X-Total-Running`,
   * the running sandboxes before pagination: probes and other templates count too), or
   * null when E2B gave no count. A count is advisory: two owners may read it at once, so
   * E2B's own refusal (`noRoom`) is what holds the plan, and an unknown count means "try".
   */
  async running(): Promise<number | null> {
    const { headers } = await this.api("GET", "/v2/sandboxes?state=running&limit=1");
    const total = headers.get("x-total-running");
    return total !== null && /^[0-9]{1,9}$/.test(total) ? Number(total) : null;
  }

  /**
   * Every sandbox of the team, running or paused, whose metadata holds `key`=`value`: the
   * ids, page by page (`x-next-token`) to the end.
   */
  async list(key: string, value: string): Promise<string[]> {
    const ids: string[] = [];
    let next: string | null = null;
    do {
      const query = new URLSearchParams({ metadata: `${encodeURIComponent(key)}=${
        encodeURIComponent(value)}`, state: "running,paused" });
      if (next !== null) {
        query.set("nextToken", next);
      }
      const { json: page, headers } = await this.api("GET", `/v2/sandboxes?${query}`);
      if (!Array.isArray(page) || !page.every((each) => object(each)
                                              && typeof each["sandboxID"] === "string")) {
        throw new E2bError(502, null, "E2B's list is out of shape");
      }
      for (const each of page as { sandboxID: string; metadata?: unknown }[]) {
        const metadata = object(each.metadata) ? each.metadata : {};
        if (metadata[key] === value) {  // E2B's own filter, checked
          ids.push(sandboxId(each.sandboxID));
        }
      }
      next = headers.get("x-next-token");
    } while (next !== null && next !== "");
    return ids;
  }
}
