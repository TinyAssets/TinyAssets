/**
 * The runner's posts, held exactly before anything reads them: a run's events (its frames
 * numbered from 1 and rising, and an optional end: `runEvents`, and a sign-in's frames,
 * `signinEvents`) and its sandbox's engines' sign-in reports (`signinReport`).
 */
import { ENGINE_SIGNINS, SIGNIN_MODELS_CHARS } from "../../contract/src/limits";
import { EVENT_FRAMES_MAX, eventsBounded, FRAME_UNITS_MAX } from "../../contract/src/run-events";
import type { PostedFrame } from "./runs";
import { Refusal } from "../../contract/src/refusal";
import { bundledSources } from "../../sources/src/catalog";
import { fields } from "../../contract/src/doors";

/** The runner's report, exactly: frames numbered from 1 and rising, and an optional end. */
export function eventsRequest(r: Record<string, unknown>): {
  frames: PostedFrame[]; end: { exit_code: number | null } | null } {
  const frames = r["frames"];
  if (!Array.isArray(frames)) {
    throw new Refusal("invalid", "invalid run events");
  }
  if (!eventsBounded(r)) {
    throw new Refusal("invalid", `a post is at most ${EVENT_FRAMES_MAX} frames, each at most ` +
                      `${FRAME_UNITS_MAX} units of JSON`);
  }
  let last = 0;
  for (const frame of frames) {
    const f = fields(frame, ["seq", "at", "event"], [], "invalid run events");
    if (typeof f["seq"] !== "number" || !Number.isSafeInteger(f["seq"]) || f["seq"] <= last
        || typeof f["at"] !== "number" || !Number.isFinite(f["at"])) {
      throw new Refusal("invalid", "a frame's seq rises from 1, and its at is a time");
    }
    last = f["seq"];
  }
  let end: { exit_code: number | null } | null = null;
  if (r["end"] !== undefined) {
    const e = fields(r["end"], ["exit_code"], [], "invalid run events");
    const code = e["exit_code"];
    if (code !== null && (typeof code !== "number" || !Number.isSafeInteger(code))) {
      throw new Refusal("invalid", "an end's exit_code is a whole number, or null");
    }
    end = { exit_code: code as number | null };
  }
  return { frames: frames as PostedFrame[], end };
}

/** The engine, its reports in one batch at most `ENGINE_SIGNINS`, each engine once. */
export function signinRequest(r: Record<string, unknown>):
    { engine: string; signed_in: boolean; models: string[] }[] {
  const signins = r["signins"];
  if (!Array.isArray(signins) || signins.length === 0 || signins.length > ENGINE_SIGNINS) {
    throw new Refusal("invalid", `a sign-in report names 1 to ${ENGINE_SIGNINS} engines`);
  }
  const sources = bundledSources();
  const seen = new Set<string>();
  return signins.map((entry) => {
    const f = fields(entry, ["engine", "signed_in"], ["models"], "invalid sign-in report");
    const engine = f["engine"];
    const cli = typeof engine === "string" ? sources.get(engine)?.cli : undefined;
    if (typeof engine !== "string" || cli === undefined || seen.has(engine)
        || !cli.credential.includes("vendor_login_in_sandbox")) {
      throw new Refusal("invalid", "a sign-in report names engines that sign in, each once");
    }
    seen.add(engine);
    if (typeof f["signed_in"] !== "boolean") {
      throw new Refusal("invalid", "signed_in is true or false");
    }
    const models = f["models"] ?? [];
    if (!Array.isArray(models) || models.some((model) => typeof model !== "string"
                                               || !/^[!-~]+$/.test(model))
        || JSON.stringify(models).length > SIGNIN_MODELS_CHARS) {
      throw new Refusal("invalid",
                        `models are model ids, at most ${SIGNIN_MODELS_CHARS} characters ` +
                        "as JSON");
    }
    return { engine, signed_in: f["signed_in"], models: models as string[] };
  });
}
