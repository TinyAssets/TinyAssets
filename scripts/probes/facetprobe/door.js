/**
 * ta-facetprobe-door: the facet probe's only way in (facet-probe.md), with no public URL.
 *
 * It is a Worker with no route and no workers.dev URL, holding one Workflow, `Drive`
 * (Workflow name `ta-facetprobe-door`). drive.mjs starts one Workflow instance per call
 * through the Cloudflare API (Workers Scripts: Edit) and reads the instance's output, so
 * nothing on the internet can reach a probe object. Each instance makes one call:
 *
 *   params { run, copy, object, method, params }
 *
 * `copy` names the probe copy (shape.mjs COPIES), whose root class this Worker binds by
 * script_name, as the doors bind OwnerDO; `method` is one of METHODS. `q4` runs here, in
 * the instance: it pings the parent while a facet burns, throws or runs out of memory.
 * The output is `{ result }` or `{ error }`; the step is never retried.
 *
 * `fetch` is for local.mjs only: it answers only when LOCAL_KEY is bound, which no deploy
 * does, and runs the same Workflow through its binding.
 */
import { WorkflowEntrypoint } from "cloudflare:workers";

import { COPIES, SHAPE } from "./shape.mjs";
import { errText, stats } from "./stats.mjs";

const METHODS = Object.freeze([
  "whoami", "q2", "q3", "q5", "q6", "q4", "fillStart", "fillStatus", "fillSizes", "deleteFacet", "q7",
]);
const NAME = /^[\w.-]{1,64}$/;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Q4, from outside the parent: baseline pings, then pings while the facet's case runs. */
async function q4(stub, { kase, source, ms, iters, boundMs }) {
  const instances = new Set();
  const pinger = () => {
    const samples = [];
    let errors = 0;
    let firstError = null;
    const ping = async () => {
      const at = Date.now();
      try {
        instances.add((await stub.ping()).instance);
        samples.push(Date.now() - at);
      } catch (error) {
        errors += 1;
        firstError ??= errText(error);
      }
    };
    return { ping, read: () => ({ ...stats(samples), errors, firstError }) };
  };
  const base = pinger();
  for (let i = 0; i < SHAPE.q4BaselinePings; i += 1) {
    await base.ping();
  }
  const started = await stub.q4Start({ kase, source, ms, iters });
  const during = pinger();
  const until = Date.now() + boundMs;
  let settled = null;
  for (let i = 1; Date.now() < until; i += 1) {
    await during.ping();
    await sleep(SHAPE.q4PingGapMs);
    if (i % 10 === 0) {
      const read = await stub.q4Outcome({ kase }).catch(() => null);
      if (read?.outcome) {
        settled = read.outcome;
        break;
      }
    }
  }
  let after;
  try {
    after = await stub.q4Outcome({ kase, source, probeFacet: true });
  } catch (error) {
    after = { error: errText(error) };
  }
  return { kase, started, baseline: base.read(), during: during.read(), instances: [...instances],
           settledInBound: Boolean(settled), after };
}

/** One call, as `{ result }` or `{ error }`. */
export async function dispatch(env, call) {
  const { copy, object, method, params } = call ?? {};
  const binding = env[COPIES[copy]?.binding];
  if (!binding || !NAME.test(object ?? "") || !METHODS.includes(method)) {
    return { error: `refused: ${copy}/${object}/${method}` };
  }
  try {
    const stub = binding.getByName(object);
    const result = method === "q4" ? await q4(stub, params ?? {}) : await stub[method](params ?? {});
    return { result };
  } catch (error) {
    return { error: errText(error) };
  }
}

export class Drive extends WorkflowEntrypoint {
  async run(event, step) {
    const call = event.payload ?? {};
    return step.do(`${call.copy}/${call.object}/${call.method}`,
                   { retries: { limit: 0, delay: "1 second" }, timeout: "15 minutes" },
                   () => dispatch(this.env, call));
  }
}

export default {
  async fetch(request, env) {
    if (!env.LOCAL_KEY || request.headers.get("x-probe-key") !== env.LOCAL_KEY) {
      return Response.json({ error: "forbidden" }, { status: 403 });
    }
    const call = await request.json().catch(() => null);
    const instance = await env.DRIVE.create({ params: call });
    for (;;) {
      const status = await instance.status();
      if (status.status === "complete") {
        return Response.json(status.output);
      }
      if (["errored", "terminated"].includes(status.status)) {
        return Response.json({ error: `instance ${status.status}: ${JSON.stringify(status.error)}` }, { status: 502 });
      }
      await sleep(50);
    }
  },
};
