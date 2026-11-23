/**
 * Each owner door's request whose row lists its fields, and each answer whose row
 * declares one, by door: written by `npm run generate` from the door table
 * (`doors.json`), which says what each means. Edit the table, never this file.
 */

export interface DoorRequests {
  readonly inboxDigest: {
    readonly limit: number;
    readonly after?: string;
    readonly max_bytes?: number;
    readonly head_chars?: number;
  };
  readonly boardDigest: {
    readonly limit: number;
    readonly after?: string;
    readonly head_chars?: number;
  };
  readonly mailboxRead: {
    readonly limit: number;
    readonly max_bytes?: number;
    readonly pad_bytes?: number;
    readonly body_offset?: number;
    readonly message_id?: string;
  };
  readonly mailboxAck: { readonly through: number };
  readonly roomPost: {
    readonly room_id: string;
    readonly client_message_id: string;
    readonly body: string;
    readonly parent_id: string | null;
  };
  readonly outsideAgents: { readonly after?: string };
  readonly registerPropose: {
    readonly display_name: string;
    readonly delivery_method: string;
    readonly recipe_id: string | null;
    readonly command_center_id?: string;
  };
  readonly policyPropose: {
    readonly change: Readonly<Record<string, unknown>>;
    readonly request_id?: string;
  };
  readonly collectionGet: {
    readonly collection: string;
    readonly row_id: string;
    readonly private?: boolean;
    readonly body_offset?: number;
    readonly etag?: string;
  };
  readonly collectionPull: {
    readonly collection: string;
    readonly limit: number;
    readonly after?: string;
  };
  readonly collectionHistory: {
    readonly collection: string;
    readonly row_id: string;
    readonly private?: boolean;
    readonly before?: string;
    readonly body_offset?: number;
  };
  readonly collectionWrite: {
    readonly collection: string;
    readonly row_id: string;
    readonly body: string;
    readonly expected_etag: string;
    readonly private: boolean;
    readonly history_op?: string;
  };
  readonly collectionDelete: {
    readonly collection: string;
    readonly row_id: string;
    readonly expected_etag: string;
    readonly private?: boolean;
  };
  readonly collectionClear: { readonly collection: string; readonly writer: string | null };
  readonly fileStat: { readonly path: string };
  readonly fileList: {
    readonly dir: string;
    readonly limit: number;
    readonly after?: string;
    readonly head_chars?: number;
  };
  readonly fileText: {
    readonly path: string;
    readonly offset?: number;
    readonly max_bytes?: number;
  };
  readonly fileSearch: { readonly query: string; readonly cursor?: string };
  readonly fileFetch: { readonly id: string };
  readonly fileAppend: { readonly path: string; readonly content: string };
  readonly fileWrite:
    | {
      readonly path: string;
      readonly expected_etag: string | null;
      readonly content: string | Uint8Array;
      readonly mode?: number;
    }
    | {
      readonly path: string;
      readonly expected_etag: string;
      readonly content: string;
      readonly mode?: number;
    };
  readonly fileEdit: {
    readonly path: string;
    readonly old_text: string;
    readonly new_text: string;
    readonly expected_etag: string;
  };
  readonly fileHistory: { readonly path: string; readonly limit: number; readonly before?: number };
  readonly fileMkdir: { readonly path: string; readonly expected_etag: string | null };
  readonly fileRemove:
    | { readonly path: string; readonly expected_etag: string | null; readonly recursive: boolean }
    | { readonly path: string; readonly expected_etag: string; readonly recursive: boolean };
  readonly fileRename:
    | {
      readonly from: string;
      readonly to: string;
      readonly expected_etag: string | null;
      readonly replace: boolean;
    }
    | {
      readonly from: string;
      readonly to: string;
      readonly expected_etag: string;
      readonly replace: boolean;
    };
  readonly fileRestore:
    | { readonly path: string; readonly version: number; readonly expected_etag: string | null }
    | { readonly path: string; readonly version: number; readonly expected_etag: string };
  readonly agentSubmit: {
    readonly message: string;
    readonly client_send_id: string;
    readonly agent_id?: string;
    readonly conversation_id?: string;
    readonly wait_ms?: number;
  };
  readonly agentRun: {
    readonly agent: string;
    readonly input: string;
    readonly at?: number;
    readonly cron?: string;
  };
  readonly agentStatus: { readonly run_id?: string; readonly schedule_id?: string };
  readonly agentCancel: { readonly run_id?: string; readonly schedule_id?: string };
  readonly codeRun: { readonly script: string };
  readonly ownerExport: { readonly cursor?: string | null; readonly export_id?: string };
  readonly exportChunks: { readonly export_id: string; readonly sha256s: readonly unknown[] };
  readonly policyWrite: {
    readonly expected_generation: number;
    readonly policy: Readonly<Record<string, unknown>>;
  };
  readonly policyProposal: { readonly request_id: string };
  readonly policyApply: { readonly card_id: string; readonly digest: string };
  readonly policyDecline: { readonly card_id: string; readonly digest: string };
  readonly policyWakeLimits: {
    readonly agent_id: string;
    readonly max_wakes_per_hour: number | null;
    readonly batch_window_s: number;
    readonly expected_generation: number;
  };
  readonly actionApprove: { readonly card_id: string; readonly digest: string };
  readonly actionRefuse: { readonly card_id: string; readonly digest: string };
  readonly grantSet: {
    readonly client: string;
    readonly source: string;
    readonly mode: string;
    readonly scopes: readonly unknown[];
    readonly expected_revision: number;
    readonly name: string;
    readonly profile?: string;
    readonly agent?: string;
  };
  readonly grantRevoke: { readonly client: string };
  readonly grantForget: { readonly client: string };
  readonly grantForgetRevoked: { readonly clients: readonly unknown[] };
  readonly keyMint: {
    readonly key_id: string;
    readonly name: string;
    readonly scopes: readonly unknown[];
  };
  readonly ownerErase: { readonly confirm: string };
  readonly erasureBegin: { readonly confirm: string };
  readonly inboundAccept: {
    readonly event_id: string;
    readonly event_at: number;
    readonly body: Uint8Array;
    readonly headers: Readonly<Record<string, unknown>>;
  };
  readonly fileUploadTarget: { readonly intent_id: string };
  readonly fileUploadStored: { readonly intent_id: string; readonly chunks: readonly unknown[] };
  readonly registerAgent:
    | { readonly card_id: string; readonly digest: string }
    | {
      readonly command_center_id: string;
      readonly display_name: string;
      readonly delivery_method: string;
      readonly recipe_id: string | null;
    };
  readonly registerDecline: { readonly card_id: string; readonly digest: string };
  readonly disableAgent: { readonly agent_id: string; readonly reason: string };
  readonly agentDoorbell: {
    readonly agent_id: string;
    readonly kind: string;
    readonly subscription_id: string;
    readonly board: boolean;
    readonly rooms: readonly unknown[];
    readonly blob: string;
    readonly kid: string;
    readonly version: number;
  };
  readonly agentDoorbellEnd: { readonly subscription_id: string };
  readonly deleteAgent: { readonly agent_id: string };
  readonly mailChain: {
    readonly message_id: string;
    readonly limit: number;
    readonly after?: number;
    readonly body_offset?: number;
    readonly body_message_id?: string;
  };
  readonly wakeSubscribe: {
    readonly subscription_id: string;
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
    readonly blob: string;
    readonly kid: string;
    readonly version: number;
    readonly expires_at: number | null;
  };
  readonly wakeUnsubscribe: { readonly subscription_id: string };
  readonly fileRead: { readonly path: string; readonly etag?: string };
  readonly fileReadChunks: {
    readonly path: string;
    readonly version: number;
    readonly first: number;
    readonly count: number;
  };
  readonly fileUploadBegin: { readonly size: number; readonly chunks: readonly unknown[] };
  readonly fileUploadChunks: { readonly intent_id: string; readonly chunks: readonly unknown[] };
  readonly fileUploadCommit: {
    readonly intent_id: string;
    readonly path: string;
    readonly expected_etag: string | null;
    readonly mode?: number | null;
  };
  readonly fileUploadAbort: { readonly intent_id: string };
  readonly fileRedact: { readonly path: string; readonly version: number };
  readonly fileHistoryPurge: { readonly path: string };
  readonly fileChanges: { readonly after: number; readonly limit: number };
  readonly runEvents: {
    readonly frames: readonly unknown[];
    readonly end?: Readonly<Record<string, unknown>>;
  };
  readonly runDecide: {
    readonly check?: boolean;
    readonly tool_name?: string;
    readonly tool_input?:
      string | number | boolean | Readonly<Record<string, unknown>> | readonly unknown[] | null;
  };
  readonly signinStart: {
    readonly engine: string;
    readonly authority?: string;
    readonly mode?: string;
  };
  readonly signinCode: { readonly id: string; readonly code: string };
  readonly signinCancel: { readonly id: string };
  readonly runList: { readonly before?: number; readonly limit?: number };
  readonly signinReport: { readonly signins: readonly unknown[] };
  readonly socketTicket: { readonly conversation_id: string };
  readonly restoreBegin: { readonly namespace: string; readonly gen: string };
  readonly restorePin: { readonly namespace: string; readonly gen: string };
  readonly restoreWipe: { readonly confirm: string };
  readonly restoreNames: { readonly from: number };
  readonly restoreHold: { readonly namespace: string; readonly gen: string };
  readonly fileSyncCommit: {
    readonly lease_id: string;
    readonly epoch: number;
    readonly ops: readonly unknown[];
  };
  readonly fileSyncUploadBegin: {
    readonly lease_id: string;
    readonly epoch: number;
    readonly size: number;
    readonly chunks: readonly unknown[];
  };
  readonly fileSyncUploadChunks: {
    readonly lease_id: string;
    readonly epoch: number;
    readonly intent_id: string;
    readonly chunks: readonly unknown[];
  };
  readonly fileSyncUploadAbort: {
    readonly lease_id: string;
    readonly epoch: number;
    readonly intent_id: string;
  };
  readonly importRows: { readonly rows: readonly unknown[] };
  readonly importFiles: { readonly entries: readonly unknown[] };
  readonly importFileBegin: {
    readonly path: string;
    readonly size: number;
    readonly mode: number;
    readonly content_id: string;
    readonly chunks: readonly unknown[];
  };
  readonly importFileStored: { readonly intent_id: string; readonly chunks: readonly unknown[] };
  readonly importFileCommit: {
    readonly intent_id: string;
    readonly path: string;
    readonly mode: number;
    readonly content_id: string;
    readonly mtime_ms: number;
  };
  readonly importNotice: { readonly detail: string };
  readonly importCheck: { readonly part: string; readonly page: number };
}

/** What the doors read of each answer its row declares: its fields, but `unread` ones. */
export interface DoorAnswers {
  readonly outsideAgents: { readonly owner_agents: readonly unknown[] };
  readonly collectionGet: { readonly row: object | null };
  readonly fileStat: { readonly entry: object | null };
  readonly sessionOpen: { readonly session: string };
  readonly sessionClose: { readonly closed: number };
  readonly sandboxBilled: { readonly counted: boolean };
  readonly sandboxDays: { readonly days: readonly unknown[] };
  readonly ownerExport: {
    readonly export_id: string;
    readonly lines: readonly unknown[];
    readonly next: string | null;
    readonly pending: boolean;
  };
  readonly dataKeyRewrap: {
    readonly rewrapped: boolean;
    readonly roots: number;
    readonly left: number;
    readonly kid: string | null;
  };
  readonly gatewayReseal: {
    readonly resealed: number;
    readonly left: number;
    readonly kid: string | null;
  };
  readonly policyRead: {
    readonly generation: number;
    readonly policy: object;
    readonly notes: readonly unknown[];
    readonly hooks: object;
    readonly codemode: object;
  };
  readonly policyProposals: { readonly cards: readonly unknown[] };
  readonly grantList: { readonly clients: readonly unknown[] };
  readonly grantRevoke: {
    readonly client: string;
    readonly revoked: boolean;
    readonly generation: number;
    readonly revision: number;
  };
  readonly grantForget: { readonly forgotten: boolean };
  readonly keyMint: {
    readonly name: string;
    readonly created_at: number;
    readonly scopes: readonly unknown[];
  };
  readonly keyList: { readonly keys: readonly unknown[] };
  readonly ownerErase: { readonly erasing: boolean };
  readonly erasureBegin: { readonly erasing: boolean };
  readonly erasureOutside: {
    readonly keys: readonly unknown[];
    readonly routes: readonly unknown[];
  } | null;
  readonly inboundCreate: {
    readonly binding_id: string;
    readonly revision: number;
    readonly route: string;
  };
  readonly inboundSet: {
    readonly binding_id: string;
    readonly revision: number;
    readonly route: string;
  };
  readonly inboundRevoke: { readonly route: string | null };
  readonly inboundAccept: {
    readonly state: string;
    readonly receipt?: string;
    readonly deduped?: boolean;
    readonly error?: string;
  };
  readonly fileUploadTarget: {
    readonly namespace: string;
    readonly key: string;
    readonly chunks: readonly unknown[];
    readonly pending: readonly unknown[];
  };
  readonly registerAgent: { readonly agent_id: string };
  readonly registerProposals: { readonly cards: readonly unknown[] };
  readonly connectionPrepare: {
    readonly connection: string;
    readonly version: number;
    readonly flow?: object;
  };
  readonly flowFinish: {
    readonly connection: string;
    readonly version: number;
    readonly verifier: string;
    readonly redirect_uri: string;
    readonly source: string;
  };
  readonly noticeList: { readonly notices: readonly unknown[] };
  readonly starterReceipts: { readonly version: number };
  readonly admissionSet: { readonly state: string };
  readonly ownerState: {
    readonly state: string;
    readonly reasons: readonly unknown[];
    readonly tier: string;
    readonly restore: string | null;
    readonly runs: number;
    readonly stored_bytes: number;
  };
  readonly toolProfile: {
    readonly profile: string;
    readonly tools: readonly unknown[];
    readonly withheld: readonly unknown[];
  };
  readonly definitionRead: {
    readonly agent: string;
    readonly instructions: Uint8Array | null;
    readonly soul: Uint8Array | null;
    readonly skills: readonly unknown[];
  };
  readonly wakeEvents: { readonly events: readonly unknown[] };
  readonly fileRead: {
    readonly entry: object;
    readonly inline?: Uint8Array;
    readonly chunks?: readonly unknown[];
  };
  readonly fileReadChunks: { readonly chunks: readonly unknown[] };
  readonly runDecide: {
    readonly tools?: readonly unknown[];
    readonly generation?: number;
    readonly allow?: boolean;
    readonly hook?: object;
  };
  readonly signinList: { readonly engines: readonly unknown[] };
  readonly tierSet: { readonly tier: string };
  readonly tierRead: { readonly tier: string };
  readonly restoreSource: {
    readonly namespace: string | null;
    readonly generations: readonly unknown[];
    readonly state: string;
    readonly begins: boolean;
    readonly restore: object | null;
  };
  readonly restoreBegin: {
    readonly step: string;
    readonly done: boolean;
    readonly failed?: string;
    readonly gen?: string;
    readonly rows?: number;
    readonly chunks?: number;
    readonly keys?: readonly unknown[];
    readonly state?: string;
    readonly reasons?: readonly unknown[];
  };
  readonly restoreStep: {
    readonly step: string;
    readonly done: boolean;
    readonly failed?: string;
    readonly gen?: string;
    readonly rows?: number;
    readonly chunks?: number;
    readonly keys?: readonly unknown[];
    readonly state?: string;
    readonly reasons?: readonly unknown[];
  };
  readonly restorePin: {
    readonly step: string;
    readonly namespace: string;
    readonly gen: string;
    readonly fresh: string | null;
    readonly written: boolean;
    readonly confirm: string | null;
  };
  readonly restoreWipe: {
    readonly step: string;
    readonly namespace: string;
    readonly gen: string;
    readonly fresh: string | null;
    readonly written: boolean;
    readonly confirm: string | null;
  };
  readonly restoreNames: {
    readonly chunks: readonly unknown[];
    readonly generations: readonly unknown[];
    readonly next: number | null;
  };
  readonly restoreHold: { readonly namespace: string; readonly held: string };
  readonly egressOpen: {
    readonly ok: boolean;
    readonly reservation?: string;
    readonly connection?: string;
    readonly blob?: string;
    readonly version?: number;
    readonly generation?: number;
    readonly metered?: boolean;
    readonly ended?: ReadableStream<Uint8Array>;
    readonly code?: string;
    readonly receipt?: string | null;
  };
  readonly egressReserve: {
    readonly ok: boolean;
    readonly metering?: object;
    readonly code?: string;
    readonly receipt?: string | null;
  };
  readonly egressReceipts: { readonly receipts: readonly unknown[] };
  readonly importFileBegin: {
    readonly skipped: boolean;
    readonly intent_id?: string;
    readonly namespace?: string;
    readonly key?: string;
    readonly chunks?: readonly unknown[];
    readonly pending?: readonly unknown[];
  };
  readonly importCheck: { readonly more: boolean };
  readonly importDiscard: { readonly namespace: string | null };
}

/** The answer fields the owner still answers, the doors reading them no more (`unread`). */
export type UnreadAnswers = Record<never, never>;
