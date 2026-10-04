/** The checked-in public snapshot: the public command center list, dated. */
export type SnapshotCommandCenter = {
  id: string;
  visibility: "public" | "metadata_only";
  phase: string;
  word_count: number;
  last_activity_at: string | null;
};

export type Snapshot = {
  fetched_at: string;
  source: string;
  command_centers: SnapshotCommandCenter[];
};
