// Written by `node platform/sources/write-records.ts` from the record files beside
// `src/`, and held to them by `contract/guards/sources-records.test.ts`: never by hand.
import anthropic from "../anthropic.json";
import cerebras from "../cerebras.json";
import chatgpt from "../chatgpt.json";
import claudeCode from "../claude-code.json";
import codex from "../codex.json";
import gemini from "../gemini.json";
import googleAiStudio from "../google_ai_studio.json";
import groq from "../groq.json";
import kimi from "../kimi.json";
import mistral from "../mistral.json";
import openai from "../openai.json";
import openrouterUserModelsV1 from "../openrouter_user_models_v1.json";
import pi from "../pi.json";
import xai from "../xai.json";

/**
 * Every record file in `platform/sources/`, as it holds it. Never written: each read is
 * a copy (`sourceRecord`).
 */
export const RECORDS: readonly unknown[] = Object.freeze([
  anthropic,
  cerebras,
  chatgpt,
  claudeCode,
  codex,
  gemini,
  googleAiStudio,
  groq,
  kimi,
  mistral,
  openai,
  openrouterUserModelsV1,
  pi,
  xai,
]);
