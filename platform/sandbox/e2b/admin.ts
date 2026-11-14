/**
 * E2B as the platform's tools reach it outside the sandbox Worker (untangle B11): the
 * template's checks (`template/checks.ts`, run by `sandbox-template.yml` and the deploy kit's
 * pair check) and the deploy kit (`deploy/deploy.ts`), over the one transport
 * (`transport.ts`), so the Worker ships none of it:
 *
 * - a template's ready build (`ready`: the alias, then the tag) and a passed pair's tags
 *   (`tag`), E2B's OpenAPI alias, tag and `AssignTemplateTagsRequest` calls;
 * - a throwaway sandbox for the template's checks (`createThrowaway`);
 * - the team's event webhooks (`webhooks`, `addWebhook`, `changeWebhook`, e2b-dev/infra's
 *   `spec/openapi.yml`, read 2026-11-04).
 *
 * It imports only the transport, by its own name, so Node's type stripping runs it in the
 * deploy kit.
 */
import type { NetworkDocument } from "../../contract/src/sandbox.ts";

import { created, E2bError, E2bTransport, type Handle, object } from "./transport.ts";

export class E2bAdmin extends E2bTransport {
  /**
   * A throwaway sandbox (the template's checks), killed, never paused, at its timeout, so
   * one a caller lost is gone at its timeout. Its network is E2B's default, open, unless
   * one is given.
   */
  async createThrowaway(request: { template: string; timeoutS: number;
                                   network: NetworkDocument | null;
                                   metadata: Readonly<Record<string, string>> }):
      Promise<Handle> {
    const { json } = await this.api("POST", "/v2/sandboxes", {
      templateID: request.template, timeout: request.timeoutS, secure: true, autoPause: false,
      ...(request.network === null ? {} : { network: request.network }),
      metadata: request.metadata,
    }, [201, 200]);
    return created(json);
  }

  /**
   * Whether `template` (`name:tag`) has a ready build in this team: the alias's template,
   * then whether it holds the tag. No such alias is false; any other failed lookup throws.
   */
  async ready(template: string): Promise<boolean> {
    const at = template.lastIndexOf(":");
    if (at < 1) {
      throw new Error("E2B_TEMPLATE is name:tag");
    }
    const alias = await this.api("GET", `/templates/aliases/${
      encodeURIComponent(template.slice(0, at))}`, undefined, [200, 404]);
    if (alias.status === 404) {
      return false;
    }
    if (!object(alias.json) || typeof alias.json["templateID"] !== "string") {
      throw new E2bError(502, null, "E2B's template alias is out of shape");
    }
    const { json } = await this.api("GET", `/templates/${
      encodeURIComponent(alias.json["templateID"])}/tags/exists?tag=${
      encodeURIComponent(template.slice(at + 1))}`);
    return object(json) && json["exists"] === true;
  }

  /** `tags` assigned to the build `target` (`name:tag`) names: how the checks mark a pair. */
  async tag(target: string, tags: readonly string[]): Promise<void> {
    await this.api("POST", "/templates/tags", { target, tags: [...tags] }, [200, 201, 204]);
  }

  /** The team's event webhooks, as E2B lists them. */
  async webhooks(): Promise<unknown[]> {
    const { json } = await this.api("GET", "/events/webhooks");
    if (!Array.isArray(json)) {
      throw new E2bError(502, null, "E2B's webhook listing holds no list");
    }
    return json;
  }

  /** One more event webhook, as `hook` describes it. */
  async addWebhook(hook: Readonly<Record<string, unknown>>): Promise<void> {
    await this.api("POST", "/events/webhooks", hook, [200, 201]);
  }

  /** The webhook `id`'s fields `change` names set. */
  async changeWebhook(id: string, change: Readonly<Record<string, unknown>>): Promise<void> {
    await this.api("PATCH", `/events/webhooks/${encodeURIComponent(id)}`, change);
  }
}
