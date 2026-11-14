/**
 * The source records' banned names, checked by the platform's guard kit (`ci/kit/`).
 *
 * The source records (`src/`: their schema, checker, catalog, run environment and sign-in
 * shapes) are a contract module beside their files: as pure as the contract, held to its
 * names, and, by the layer table, reaching the contract and their own record files alone.
 * The contract never reaches them.
 */
import type { ImportRules } from "../../ci/kit/imports";
import { IMPORTS as CONTRACT } from "../../contract/guards/rules";

export const IMPORTS: ImportRules = Object.freeze({
  worker: "sources",
  called: "the source records",
  banned: CONTRACT.banned,
});
