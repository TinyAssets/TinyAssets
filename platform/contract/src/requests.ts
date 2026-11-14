/**
 * Each owner door's request whose row lists its fields, by door: written by
 * `npm run generate` from the door table (`doors.json`), which says what each means.
 * Edit the table, never this file.
 */

/** A request of exactly the `Required` fields and any of the `Optional` ones. */
export type DoorFields<Required extends string, Optional extends string = never> =
  { readonly [Field in Required]: unknown } & { readonly [Field in Optional]?: unknown };

export interface DoorRequests {
  readonly inboxDigest: DoorFields<"limit" | "after", "max_bytes" | "head_chars">;
  readonly boardDigest: DoorFields<"limit" | "after", "head_chars">;
  readonly mailboxRead:
    DoorFields<"limit", "max_bytes" | "pad_bytes" | "body_offset" | "message_id">;
  readonly mailboxAck: DoorFields<"through">;
  readonly roomPost: DoorFields<"room_id" | "client_message_id" | "body" | "parent_id">;
  readonly outsideAgents: DoorFields<never, "after">;
  readonly registerPropose:
    DoorFields<"command_center_id" | "display_name" | "delivery_method" | "recipe_id">;
  readonly policyPropose: DoorFields<"change", "request_id">;
  readonly collectionGet: DoorFields<"collection" | "row_id", "body_offset" | "etag">;
  readonly collectionPull: DoorFields<"collection" | "after" | "limit">;
  readonly collectionHistory: DoorFields<"collection" | "row_id", "before" | "body_offset">;
  readonly collectionWrite:
    DoorFields<"collection" | "row_id" | "body" | "expected_etag" | "private", "history_op">;
  readonly collectionDelete: DoorFields<"collection" | "row_id" | "expected_etag">;
  readonly collectionClear: DoorFields<"collection" | "writer">;
  readonly fileStat: DoorFields<"path">;
  readonly fileList: DoorFields<"dir" | "limit", "after" | "head_chars">;
  readonly fileText: DoorFields<"path", "offset" | "max_bytes">;
  readonly fileSearch: DoorFields<"query", "cursor">;
  readonly fileFetch: DoorFields<"id">;
  readonly fileAppend: DoorFields<"path" | "content">;
  readonly fileWrite: DoorFields<"path" | "expected_etag" | "content", "mode">;
  readonly fileEdit: DoorFields<"path" | "old_text" | "new_text" | "expected_etag">;
  readonly fileHistory: DoorFields<"path" | "limit", "before">;
  readonly fileMkdir: DoorFields<"path" | "expected_etag">;
  readonly fileRemove: DoorFields<"path" | "expected_etag" | "recursive">;
  readonly fileRename: DoorFields<"from" | "to" | "expected_etag" | "replace">;
  readonly fileRestore: DoorFields<"path" | "version" | "expected_etag">;
  readonly agentSubmit:
    DoorFields<"message" | "client_send_id", "agent_id" | "conversation_id" | "wait_ms">;
  readonly agentRun: DoorFields<"agent" | "input", "at" | "cron">;
  readonly agentStatus: DoorFields<never, "run_id" | "schedule_id">;
  readonly agentCancel: DoorFields<never, "run_id" | "schedule_id">;
  readonly codeRun: DoorFields<"script">;
  readonly ownerExport: DoorFields<never, "cursor" | "export_id">;
  readonly exportChunks: DoorFields<"export_id" | "sha256s">;
  readonly policyWrite: DoorFields<"expected_generation" | "policy", "proposal">;
  readonly policyProposal: DoorFields<"request_id">;
  readonly policyWakeLimits:
    DoorFields<"agent_id" | "max_wakes_per_hour" | "batch_window_s" | "expected_generation">;
  readonly grantSet:
    DoorFields<"client" | "source" | "mode" | "scopes" | "expected_revision" | "name", "profile">;
  readonly grantRevoke: DoorFields<"client">;
  readonly grantForget: DoorFields<"client">;
  readonly grantForgetRevoked: DoorFields<"clients">;
  readonly keyMint: DoorFields<"key_id" | "name" | "scopes", "proposal">;
  readonly ownerErase: DoorFields<"confirm">;
  readonly inboundAccept: DoorFields<"event_id" | "event_at" | "body" | "headers">;
  readonly fileUploadTarget: DoorFields<"intent_id">;
  readonly fileUploadStored: DoorFields<"intent_id" | "chunks">;
  readonly registerAgent:
    | DoorFields<"card_id" | "digest">
    | DoorFields<"command_center_id" | "display_name" | "delivery_method" | "recipe_id">;
  readonly registerProposal: DoorFields<"card_id">;
  readonly disableAgent: DoorFields<"agent_id" | "reason">;
  readonly agentDoorbell:
    DoorFields<"agent_id" | "kind" | "subscription_id" | "board" | "rooms" | "blob" | "kid"
      | "version">;
  readonly agentDoorbellEnd: DoorFields<"subscription_id">;
  readonly deleteAgent: DoorFields<"agent_id">;
  readonly mailChain:
    DoorFields<"message_id" | "limit", "after" | "body_offset" | "body_message_id">;
  readonly wakeSubscribe:
    DoorFields<"subscription_id" | "name" | "arguments" | "blob" | "kid" | "version"
      | "expires_at">;
  readonly wakeUnsubscribe: DoorFields<"subscription_id">;
  readonly fileRead: DoorFields<"path", "etag">;
  readonly fileReadChunks: DoorFields<"path" | "version" | "first" | "count">;
  readonly fileUploadBegin: DoorFields<"size" | "chunks">;
  readonly fileUploadChunks: DoorFields<"intent_id" | "chunks">;
  readonly fileUploadCommit: DoorFields<"intent_id" | "path" | "expected_etag", "mode">;
  readonly fileUploadAbort: DoorFields<"intent_id">;
  readonly fileRedact: DoorFields<"path" | "version">;
  readonly fileHistoryPurge: DoorFields<"path">;
  readonly fileChanges: DoorFields<"after" | "limit">;
  readonly runEvents: DoorFields<"frames", "end">;
  readonly signinStart: DoorFields<"engine", "authority" | "region" | "mode">;
  readonly signinCode: DoorFields<"id" | "code">;
  readonly signinCancel: DoorFields<"id">;
  readonly runList: DoorFields<never, "before" | "limit">;
  readonly signinReport: DoorFields<"signins">;
  readonly socketTicket: DoorFields<"conversation_id">;
  readonly fileSyncCommit: DoorFields<"lease_id" | "epoch" | "ops">;
  readonly fileSyncUploadBegin: DoorFields<"lease_id" | "epoch" | "size" | "chunks">;
  readonly fileSyncUploadChunks: DoorFields<"lease_id" | "epoch" | "intent_id" | "chunks">;
  readonly fileSyncUploadAbort: DoorFields<"lease_id" | "epoch" | "intent_id">;
  readonly importRows: DoorFields<"rows">;
  readonly importFiles: DoorFields<"entries">;
  readonly importFileBegin: DoorFields<"path" | "size" | "mode" | "content_id" | "chunks">;
  readonly importFileStored: DoorFields<"intent_id" | "chunks">;
  readonly importFileCommit: DoorFields<"intent_id" | "path" | "mode" | "content_id" | "mtime_ms">;
  readonly importNotice: DoorFields<"detail">;
  readonly importCheck: DoorFields<"part" | "page">;
}
