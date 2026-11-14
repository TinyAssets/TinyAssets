/**
 * The live names the cutover import's job reaches, by what each names: what the deploy kit
 * writes for it (`deploy/facts.ts --import`, `IMPORT_NAMES`, each read from where it is
 * written) and the job reads (`import/stage.ts`'s `namesOf`, from the window's `facts.json`).
 * One list, so the two cannot disagree; a leaf, so the job takes it without the kit. Deleted at
 * the cutover with the importer.
 */
export const IMPORT_NAME_KEYS = Object.freeze(["staging_bucket", "queue", "dead_letter_queue",
                                               "files_bucket", "directory",
                                               "final_backup_bucket"] as const);
export type ImportNames = Readonly<Record<(typeof IMPORT_NAME_KEYS)[number], string>>;
