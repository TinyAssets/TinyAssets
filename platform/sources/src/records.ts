// Written by `npm run generate` (`write-records.ts`) from the record files in
// `records/`: never by hand.
import anthropic from "../records/anthropic.json";
import cerebras from "../records/cerebras.json";
import claudeCode from "../records/claude-code.json";
import codex from "../records/codex.json";
import gemini from "../records/gemini.json";
import googleAiStudio from "../records/google_ai_studio.json";
import groq from "../records/groq.json";
import kimi from "../records/kimi.json";
import mistral from "../records/mistral.json";
import openai from "../records/openai.json";
import openrouterUserModelsV1 from "../records/openrouter_user_models_v1.json";
import pi from "../records/pi.json";
import xai from "../records/xai.json";

/**
 * Every record file in `platform/sources/records/`, as it holds it. Never written: each
 * read is a copy (`sourceRecord`).
 */
export const RECORDS: readonly unknown[] = Object.freeze([
  anthropic,
  cerebras,
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
