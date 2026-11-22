/**
 * The owner object's typed handler map, by door: written by `npm run generate` from the
 * door table's two halves (`contract/doors.json`, `owner/doors.json`), which say what each
 * means. Edit the tables, never this file.
 */
import type { Binding } from "../../contract/src/binding";

/** Each door's module (its handler's), caller and gate, as its steps name them. */
export interface DoorRows {
  readonly inboxDigest:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "units" };
  readonly boardDigest:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly mailboxRead:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "units" };
  readonly mailboxAck:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "synced" };
  readonly roomPost:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "units" };
  readonly outsideAgents:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "units" };
  readonly registerPropose:
    { readonly module: "registrations"; readonly caller: "account"; readonly gate: "door" };
  readonly policyPropose:
    { readonly module: "policy"; readonly caller: "account"; readonly gate: "door" };
  readonly collectionGet:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly collectionPull:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly collectionHistory:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly collectionWrite:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly collectionDelete:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "units" };
  readonly collectionClear:
    { readonly module: "collections"; readonly caller: "visitors"; readonly gate: "synced" };
  readonly fileStat:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "door" };
  readonly fileList:
    { readonly module: "files"; readonly caller: "reader"; readonly gate: "units" };
  readonly fileText:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileSearch:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileFetch:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileAppend:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileWrite:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileEdit:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileHistory:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "door" };
  readonly fileUsage:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "door" };
  readonly fileMkdir:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileRemove:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileRename:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileRestore:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly agentSubmit:
    { readonly module: "runs"; readonly caller: "account"; readonly gate: "units" };
  readonly agentRun:
    { readonly module: "runs"; readonly caller: "account"; readonly gate: "units" };
  readonly agentStatus:
    { readonly module: "runs"; readonly caller: "account"; readonly gate: "door" };
  readonly agentCancel:
    { readonly module: "runs"; readonly caller: "account"; readonly gate: "units" };
  readonly codeRun:
    { readonly module: "codemode"; readonly caller: "account"; readonly gate: "units" };
  readonly sessionOpen:
    { readonly module: "sessions"; readonly caller: "owner"; readonly gate: "door" };
  readonly sessionExtend:
    { readonly module: "sessions"; readonly caller: "owner"; readonly gate: "quiet" };
  readonly sessionClose:
    { readonly module: "sessions"; readonly caller: "owner"; readonly gate: "synced" };
  readonly sessionCloseAll:
    { readonly module: "sessions"; readonly caller: "owner"; readonly gate: "units" };
  readonly sandboxBilled:
    { readonly module: "sandbox-time"; readonly caller: "none"; readonly gate: "units" };
  readonly sandboxDays:
    { readonly module: "sandbox-time"; readonly caller: "owner"; readonly gate: "door" };
  readonly storageUsage:
    { readonly module: "files"; readonly caller: "owner"; readonly gate: "door" };
  readonly ownerExport:
    { readonly module: "export"; readonly caller: "owner"; readonly gate: "units" };
  readonly exportChunks:
    { readonly module: "export"; readonly caller: "owner"; readonly gate: "units" };
  readonly exportEnd:
    { readonly module: "export"; readonly caller: "owner"; readonly gate: "quiet" };
  readonly dataKeyRewrap:
    { readonly module: "files"; readonly caller: "reserved"; readonly gate: "units" };
  readonly gatewayReseal:
    { readonly module: "connections"; readonly caller: "reserved"; readonly gate: "units" };
  readonly policyRead:
    { readonly module: "policy"; readonly caller: "owner"; readonly gate: "door" };
  readonly policyReceipts:
    { readonly module: "policy"; readonly caller: "owner"; readonly gate: "door" };
  readonly policyWrite:
    { readonly module: "policy"; readonly caller: "session"; readonly gate: "units" };
  readonly policyProposal:
    { readonly module: "policy"; readonly caller: "owner"; readonly gate: "door" };
  readonly policyWakeLimits:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "door" };
  readonly actionRequest:
    { readonly module: "actions"; readonly caller: "owner"; readonly gate: "door" };
  readonly actionApprove:
    { readonly module: "actions"; readonly caller: "session"; readonly gate: "synced" };
  readonly actionRefuse:
    { readonly module: "actions"; readonly caller: "session"; readonly gate: "door" };
  readonly grantList:
    { readonly module: "grants"; readonly caller: "owner"; readonly gate: "door" };
  readonly grantSet:
    { readonly module: "grants"; readonly caller: "session"; readonly gate: "synced" };
  readonly grantRevoke:
    { readonly module: "grants"; readonly caller: "owner"; readonly gate: "synced" };
  readonly grantForget:
    { readonly module: "grants"; readonly caller: "owner"; readonly gate: "door" };
  readonly grantForgetRevoked:
    { readonly module: "grants"; readonly caller: "session"; readonly gate: "door" };
  readonly keyMint:
    { readonly module: "keys"; readonly caller: "session"; readonly gate: "synced" };
  readonly keyList:
    { readonly module: "keys"; readonly caller: "owner"; readonly gate: "door" };
  readonly ownerErase:
    { readonly module: "erasure"; readonly caller: "session"; readonly gate: "units" };
  readonly erasureBegin:
    { readonly module: "erasure"; readonly caller: "reserved"; readonly gate: "units" };
  readonly erasureOutside:
    { readonly module: "erasure"; readonly caller: "erasure"; readonly gate: "door" };
  readonly erasureOutsideDone:
    { readonly module: "erasure"; readonly caller: "erasure"; readonly gate: "door" };
  readonly inboundList:
    { readonly module: "inbound"; readonly caller: "owner"; readonly gate: "door" };
  readonly inboundCreate:
    { readonly module: "inbound"; readonly caller: "session"; readonly gate: "door" };
  readonly inboundSet:
    { readonly module: "inbound"; readonly caller: "session"; readonly gate: "door" };
  readonly inboundRevoke:
    { readonly module: "inbound"; readonly caller: "owner"; readonly gate: "door" };
  readonly inboundAccept:
    { readonly module: "inbound"; readonly caller: "inbound"; readonly gate: "units" };
  readonly fileUploadTarget:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileUploadStored:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "door" };
  readonly registerAgent:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "synced" };
  readonly registerProposal:
    { readonly module: "registrations"; readonly caller: "owner"; readonly gate: "door" };
  readonly disableAgent:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "synced" };
  readonly agentDoorbell:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "door" };
  readonly agentDoorbellEnd:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "door" };
  readonly deleteAgent:
    { readonly module: "ledger"; readonly caller: "session"; readonly gate: "synced" };
  readonly connectionPrepare:
    { readonly module: "connections"; readonly caller: "session"; readonly gate: "synced" };
  readonly connectionCommit:
    { readonly module: "connections"; readonly caller: "session"; readonly gate: "synced" };
  readonly connectionRevoke:
    { readonly module: "connections"; readonly caller: "session"; readonly gate: "units" };
  readonly connectionList:
    { readonly module: "connections"; readonly caller: "session"; readonly gate: "door" };
  readonly flowFinish:
    { readonly module: "connections"; readonly caller: "session"; readonly gate: "synced" };
  readonly noticeList:
    { readonly module: "notices"; readonly caller: "owner"; readonly gate: "door" };
  readonly starterReceipts:
    { readonly module: "starter"; readonly caller: "owner"; readonly gate: "door" };
  readonly admissionSet:
    { readonly module: "admission"; readonly caller: "reserved"; readonly gate: "units" };
  readonly ownerState:
    { readonly module: "admission"; readonly caller: "reserved"; readonly gate: "units" };
  readonly toolProfile:
    { readonly module: "codemode"; readonly caller: "account"; readonly gate: "door" };
  readonly definitionRead:
    { readonly module: "definition"; readonly caller: "account"; readonly gate: "units" };
  readonly mailChain:
    { readonly module: "ledger"; readonly caller: "owner"; readonly gate: "units" };
  readonly wakeEvents:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "door" };
  readonly wakeSubscribe:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "synced" };
  readonly wakeUnsubscribe:
    { readonly module: "ledger"; readonly caller: "account"; readonly gate: "door" };
  readonly fileRead:
    { readonly module: "files"; readonly caller: "reader"; readonly gate: "door" };
  readonly fileReadChunks:
    { readonly module: "files"; readonly caller: "reader"; readonly gate: "units" };
  readonly fileUploadBegin:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileUploadChunks:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "units" };
  readonly fileUploadCommit:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileUploadAbort:
    { readonly module: "files"; readonly caller: "account"; readonly gate: "filing" };
  readonly fileRedact:
    { readonly module: "files"; readonly caller: "owner"; readonly gate: "filing" };
  readonly fileHistoryPurge:
    { readonly module: "files"; readonly caller: "owner"; readonly gate: "filing" };
  readonly fileChanges:
    { readonly module: "files"; readonly caller: "changes"; readonly gate: "door" };
  readonly runEvents:
    { readonly module: "runs"; readonly caller: "run"; readonly gate: "units" };
  readonly runDecide:
    { readonly module: "actions"; readonly caller: "account"; readonly gate: "door" };
  readonly signinStart:
    { readonly module: "signins"; readonly caller: "owner"; readonly gate: "units" };
  readonly signinEvents:
    { readonly module: "signins"; readonly caller: "runner"; readonly gate: "units" };
  readonly signinCode:
    { readonly module: "signins"; readonly caller: "owner"; readonly gate: "units" };
  readonly signinCancel:
    { readonly module: "signins"; readonly caller: "owner"; readonly gate: "units" };
  readonly signinList:
    { readonly module: "signins"; readonly caller: "owner"; readonly gate: "door" };
  readonly runList:
    { readonly module: "runs"; readonly caller: "account"; readonly gate: "door" };
  readonly signinReport:
    { readonly module: "signins"; readonly caller: "runner"; readonly gate: "door" };
  readonly sandboxRung:
    { readonly module: "runs"; readonly caller: "none"; readonly gate: "units" };
  readonly socketTicket:
    { readonly module: "relay"; readonly caller: "owner"; readonly gate: "door" };
  readonly socketsEnd:
    { readonly module: "relay"; readonly caller: "owner"; readonly gate: "units" };
  readonly tierSet:
    { readonly module: "admission"; readonly caller: "reserved"; readonly gate: "door" };
  readonly tierRead:
    { readonly module: "admission"; readonly caller: "owner"; readonly gate: "door" };
  readonly restoreSource:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreBegin:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreStep:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restorePin:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreWipe:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreRelease:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreNames:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly restoreHold:
    { readonly module: "restore"; readonly caller: "reserved"; readonly gate: "units" };
  readonly egressOpen:
    { readonly module: "egress"; readonly caller: "egress"; readonly gate: "units" };
  readonly egressReserve:
    { readonly module: "egress"; readonly caller: "egress"; readonly gate: "door" };
  readonly egressBeat:
    { readonly module: "egress"; readonly caller: "egress"; readonly gate: "door" };
  readonly egressSettle:
    { readonly module: "egress"; readonly caller: "egress"; readonly gate: "door" };
  readonly egressReceipts:
    { readonly module: "egress"; readonly caller: "owner"; readonly gate: "door" };
  readonly fileSyncCommit:
    { readonly module: "files"; readonly caller: "lease"; readonly gate: "filing" };
  readonly fileSyncUploadBegin:
    { readonly module: "files"; readonly caller: "sync"; readonly gate: "filing" };
  readonly fileSyncUploadChunks:
    { readonly module: "files"; readonly caller: "sync"; readonly gate: "units" };
  readonly fileSyncUploadAbort:
    { readonly module: "files"; readonly caller: "sync"; readonly gate: "filing" };
  readonly importRows:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "units" };
  readonly importFiles:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "filing" };
  readonly importFileBegin:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "units" };
  readonly importFileStored:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "door" };
  readonly importFileCommit:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "filing" };
  readonly importNotice:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "door" };
  readonly importCheck:
    { readonly module: "import"; readonly caller: "import"; readonly gate: "units" };
  readonly importDiscard:
    { readonly module: "import"; readonly caller: "discard"; readonly gate: "units" };
}

/**
 * The doors that take their caller's stop after their request (their rows' `stop`), each
 * handler handed it as its door's `left`.
 */
export type StopDoors = "codeRun";

/** The handler of `door` in a map of modules' handlers `H`, by its row's module. */
type HandlerIn<H, D extends keyof DoorRows> = DoorRows[D]["module"] extends infer M
  ? M extends keyof H ? D extends keyof H[M] ? H[M][D] : never : never : never;
/** The request a door's handler in `H` takes (its call's `request`). */
export type RequestIn<H, D extends keyof DoorRows> =
  HandlerIn<H, D> extends (o: never, call: infer C) => unknown
    ? C extends { readonly request: infer R } ? R : never : never;
/** What a door's handler in `H` answers. */
export type AnswerIn<H, D extends keyof DoorRows> =
  HandlerIn<H, D> extends (...args: never) => infer T ? Awaited<T> : never;

/** Each door as the owner object's method, typed by its handler in `H`. */
export interface DoorMethods<H> {
  /**
   * Read what is new for this agent in one call: its unacknowledged mail, oldest first, and the
   * rows of the owner's rooms changed after a cursor, each with the head of its body. Acknowledge
   * mail with mailbox_ack; a mail body cut to fit (truncated true) is read on with mailbox_read's
   * body_offset and message_id; read a whole row with collection_get. An answer with reset true
   * means a deletion past the cursor is no longer kept: read the rooms again whole, then go on from
   * the cursor given.
   */
  inboxDigest(binding: Binding, request: RequestIn<H, "inboxDigest">):
    Promise<AnswerIn<H, "inboxDigest">>;
  /**
   * Read the owner's board rows changed after a cursor in one call, each with its etag and the head
   * of its body. Claim a row with collection_write on its etag; read a whole row with
   * collection_get. An answer with reset true means a deletion past the cursor is no longer kept:
   * read the board again whole (collection_pull), then go on from the cursor given.
   */
  boardDigest(binding: Binding, request: RequestIn<H, "boardDigest">):
    Promise<AnswerIn<H, "boardDigest">>;
  /**
   * Read this agent's unacknowledged mailbox messages, oldest first. One answer holds at most 50000
   * characters: a message whose body does not fit is cut, with truncated true and body_next_offset;
   * read the rest by calling again with body_offset set to it and message_id set to that message's,
   * before acknowledging it.
   */
  mailboxRead(binding: Binding, request: RequestIn<H, "mailboxRead">):
    Promise<AnswerIn<H, "mailboxRead">>;
  /**
   * Acknowledge this agent's mailbox messages through a sequence number.
   */
  mailboxAck(binding: Binding, request: RequestIn<H, "mailboxAck">):
    Promise<AnswerIn<H, "mailboxAck">>;
  /**
   * Post a message to a room of the owner's account: the room_id agents_list shows for the agent to
   * reach. Mail to one of the owner's own agents starts its run, whose result is its reply.
   */
  roomPost(binding: Binding, request: RequestIn<H, "roomPost">): Promise<AnswerIn<H, "roomPost">>;
  /**
   * List the agents registered for this connection, the owner's own agents, each with the id its
   * rooms name, and which of the owner's AI apps are connected to TinyAssets, each one not
   * connected with its connect_url, the page with its connect step. One answer holds as many as
   * fit; when it carries next, read again with after set to it for the rest.
   */
  outsideAgents(binding: Binding, request?: RequestIn<H, "outsideAgents">):
    Promise<AnswerIn<H, "outsideAgents">>;
  /**
   * File this connection's agent registration for the owner to approve.
   */
  registerPropose(binding: Binding, request: RequestIn<H, "registerPropose">):
    Promise<AnswerIn<H, "registerPropose">>;
  /**
   * Propose a change to the owner's policy, for the owner to approve.
   */
  policyPropose(binding: Binding, request: RequestIn<H, "policyPropose">):
    Promise<AnswerIn<H, "policyPropose">>;
  /**
   * Read one row of a collection. One answer holds at most 50000 characters: a body that does not
   * fit is cut, with truncated true and body_next_offset; read the rest by calling again with
   * body_offset set to it and etag set to the row's etag, which refuses the read if the row changed
   * meanwhile.
   */
  collectionGet(binding: Binding, request: RequestIn<H, "collectionGet">):
    Promise<AnswerIn<H, "collectionGet">>;
  /**
   * List a collection's rows changed after a revision, as many as one answer holds (at most 50000
   * characters); go on from the cursor it gives. A first row whose body does not fit is cut
   * (truncated true): read the rest with collection_get.
   */
  collectionPull(binding: Binding, request: RequestIn<H, "collectionPull">):
    Promise<AnswerIn<H, "collectionPull">>;
  /**
   * Read the kept history of one collection row since it was made, newest first by page; pass the
   * before it gives for the older entries when the answer says more. A newest entry whose body does
   * not fit one answer is cut (truncated true): call again with the same before and body_offset set
   * to its body_next_offset.
   */
  collectionHistory(binding: Binding, request: RequestIn<H, "collectionHistory">):
    Promise<AnswerIn<H, "collectionHistory">>;
  /**
   * Write one collection row against its current etag. A new row (expected_etag "") is refused when
   * the row exists, so creating one claims an act: on the board, the first agent to create the row
   * named for an act holds it, and a conflict means another agent has it.
   */
  collectionWrite(binding: Binding, request: RequestIn<H, "collectionWrite">):
    Promise<AnswerIn<H, "collectionWrite">>;
  /**
   * Delete one collection row against its current etag.
   */
  collectionDelete(binding: Binding, request: RequestIn<H, "collectionDelete">):
    Promise<AnswerIn<H, "collectionDelete">>;
  /**
   * Delete a collection's rows, or one writer's rows.
   */
  collectionClear(binding: Binding, request: RequestIn<H, "collectionClear">):
    Promise<AnswerIn<H, "collectionClear">>;
  /**
   * Read one file's or directory's entry.
   */
  fileStat(binding: Binding, request: RequestIn<H, "fileStat">): Promise<AnswerIn<H, "fileStat">>;
  /**
   * List a directory's entries by name. With head_chars, each small file also carries the start of
   * its text, enough to read its title and description.
   */
  fileList(binding: Binding, request: RequestIn<H, "fileList">): Promise<AnswerIn<H, "fileList">>;
  /**
   * Use this to read a file of the user's TinyAssets workspace by its path, such as their profile
   * /workspace/USER.md: its UTF-8 text from offset (0 when omitted), at most max_bytes (65536 when
   * omitted, up to 4194304) and at most what one answer holds (50000 characters), cut at a
   * character. next_offset is where the next part starts, or null at the end. Its entry carries the
   * etag file_edit and file_write take.
   */
  fileText(binding: Binding, request: RequestIn<H, "fileText">): Promise<AnswerIn<H, "fileText">>;
  /**
   * Use this when the user's request may depend on what they or their other agents already saved:
   * their details, preferences, goals, decisions, notes and projects. Searches the user's
   * TinyAssets workspace by words: file paths, titles, descriptions and tags, small files' text,
   * and index lines (/workspace/index.md maps the workspace). Answers the best results, each an id
   * for fetch, a title and a url. When next_cursor is set, only part of the files were searched:
   * search again with it for the rest.
   */
  fileSearch(binding: Binding, request: RequestIn<H, "fileSearch">):
    Promise<AnswerIn<H, "fileSearch">>;
  /**
   * Use this to read one of the user's TinyAssets files a search found, by its id, the file's path:
   * its title, its text (as much as one answer holds; when metadata.truncated, file_read reads on
   * from metadata.next_offset), its url and its metadata, the etag file_edit takes among them.
   */
  fileFetch(binding: Binding, request: RequestIn<H, "fileFetch">):
    Promise<AnswerIn<H, "fileFetch">>;
  /**
   * Use this to save what the user tells you that is worth keeping, so their other agents know it
   * too: adds text at the end of a file in their TinyAssets workspace, such as a directive at the
   * end of /workspace/USER.md or a note in their knowledge, and makes the file when it is absent.
   * It never changes what is already there. The text goes in exactly as given: begin it with a
   * newline where the file needs one. A JSON, TOML or YAML file is changed with file_edit instead.
   */
  fileAppend(binding: Binding, request: RequestIn<H, "fileAppend">):
    Promise<AnswerIn<H, "fileAppend">>;
  /**
   * Write a whole file of the user's TinyAssets workspace: its text replaces what is there, at the
   * file's current etag, or "" to create a file that is not there yet. A conflict answers the file
   * as it now stands. At most 4 MiB of text.
   */
  fileWrite(binding: Binding, request: RequestIn<H, "fileWrite">):
    Promise<AnswerIn<H, "fileWrite">>;
  /**
   * Use this to correct a file of the user's TinyAssets workspace in place, such as a directive in
   * /workspace/USER.md that stopped being true: replaces the one exact occurrence of old_text with
   * new_text, at the file's current etag (from fetch, file_read or file_stat). Text found nowhere,
   * or more than once, is refused: give enough of it to be unique. A conflict answers the file as
   * it now stands.
   */
  fileEdit(binding: Binding, request: RequestIn<H, "fileEdit">): Promise<AnswerIn<H, "fileEdit">>;
  /**
   * List a path's kept versions, newest first.
   */
  fileHistory(binding: Binding, request: RequestIn<H, "fileHistory">):
    Promise<AnswerIn<H, "fileHistory">>;
  /**
   * Read the owner's file usage and quotas.
   */
  fileUsage(binding: Binding): Promise<AnswerIn<H, "fileUsage">>;
  /**
   * Make a directory against the path's current etag.
   */
  fileMkdir(binding: Binding, request: RequestIn<H, "fileMkdir">):
    Promise<AnswerIn<H, "fileMkdir">>;
  /**
   * Remove a file or directory against its current etag.
   */
  fileRemove(binding: Binding, request: RequestIn<H, "fileRemove">):
    Promise<AnswerIn<H, "fileRemove">>;
  /**
   * Rename a file or directory against its current etag.
   */
  fileRename(binding: Binding, request: RequestIn<H, "fileRename">):
    Promise<AnswerIn<H, "fileRename">>;
  /**
   * Make a kept version of a file its current content.
   */
  fileRestore(binding: Binding, request: RequestIn<H, "fileRestore">):
    Promise<AnswerIn<H, "fileRestore">>;
  /**
   * Send a message to one of the owner's agents: a run of its engine in a new conversation, or in
   * one of yours by conversation_id. A retry with the same client_send_id is the same run. Waits up
   * to wait_ms for the run to end, then answers the run as it stands; run_status reads it later.
   */
  agentSubmit(binding: Binding, request: RequestIn<H, "agentSubmit">):
    Promise<AnswerIn<H, "agentSubmit">>;
  /**
   * Launch one of the owner's agents on an input, in a fresh conversation of its own: now, at a
   * time, or at the times a cron names. Answers the run, or the schedule, at once.
   */
  agentRun(binding: Binding, request: RequestIn<H, "agentRun">): Promise<AnswerIn<H, "agentRun">>;
  /**
   * Read one of your runs, its state and its result once done, or one of your schedules.
   */
  agentStatus(binding: Binding, request: RequestIn<H, "agentStatus">):
    Promise<AnswerIn<H, "agentStatus">>;
  /**
   * Cancel one of your runs (its engine is stopped), or one of your schedules.
   */
  agentCancel(binding: Binding, request: RequestIn<H, "agentCancel">):
    Promise<AnswerIn<H, "agentCancel">>;
  /**
   * Run one JavaScript script that calls your other tools here as `await tools.<name>(args)`, each
   * held to your grant, and answer its return value and output: many tool calls in one. Its effects
   * are the effects of the tools it calls. Only while the owner's policy turns code mode on; a
   * script past its time or compute bound is stopped.
   */
  codeRun(binding: Binding, request: RequestIn<H, "codeRun">, stop: ReadableStream<Uint8Array>):
    Promise<AnswerIn<H, "codeRun">>;
  sessionOpen(binding: Binding): Promise<AnswerIn<H, "sessionOpen">>;
  sessionExtend(binding: Binding): Promise<AnswerIn<H, "sessionExtend">>;
  sessionClose(binding: Binding): Promise<AnswerIn<H, "sessionClose">>;
  sessionCloseAll(binding: Binding): Promise<AnswerIn<H, "sessionCloseAll">>;
  sandboxBilled(request: RequestIn<H, "sandboxBilled">): Promise<AnswerIn<H, "sandboxBilled">>;
  sandboxDays(binding: Binding, request: RequestIn<H, "sandboxDays">):
    Promise<AnswerIn<H, "sandboxDays">>;
  storageUsage(binding: Binding): Promise<AnswerIn<H, "storageUsage">>;
  ownerExport(binding: Binding, request: RequestIn<H, "ownerExport">):
    Promise<AnswerIn<H, "ownerExport">>;
  exportChunks(binding: Binding, request: RequestIn<H, "exportChunks">):
    Promise<AnswerIn<H, "exportChunks">>;
  exportEnd(binding: Binding, request: RequestIn<H, "exportEnd">):
    Promise<AnswerIn<H, "exportEnd">>;
  dataKeyRewrap(binding: Binding): Promise<AnswerIn<H, "dataKeyRewrap">>;
  gatewayReseal(binding: Binding): Promise<AnswerIn<H, "gatewayReseal">>;
  policyRead(binding: Binding): Promise<AnswerIn<H, "policyRead">>;
  policyReceipts(binding: Binding): Promise<AnswerIn<H, "policyReceipts">>;
  policyWrite(binding: Binding, request: RequestIn<H, "policyWrite">):
    Promise<AnswerIn<H, "policyWrite">>;
  policyProposal(binding: Binding, request: RequestIn<H, "policyProposal">):
    Promise<AnswerIn<H, "policyProposal">>;
  policyWakeLimits(binding: Binding, request: RequestIn<H, "policyWakeLimits">):
    Promise<AnswerIn<H, "policyWakeLimits">>;
  actionRequest(binding: Binding, request: RequestIn<H, "actionRequest">):
    Promise<AnswerIn<H, "actionRequest">>;
  actionApprove(binding: Binding, request: RequestIn<H, "actionApprove">):
    Promise<AnswerIn<H, "actionApprove">>;
  actionRefuse(binding: Binding, request: RequestIn<H, "actionRefuse">):
    Promise<AnswerIn<H, "actionRefuse">>;
  grantList(binding: Binding): Promise<AnswerIn<H, "grantList">>;
  grantSet(binding: Binding, request: RequestIn<H, "grantSet">): Promise<AnswerIn<H, "grantSet">>;
  grantRevoke(binding: Binding, request: RequestIn<H, "grantRevoke">):
    Promise<AnswerIn<H, "grantRevoke">>;
  grantForget(binding: Binding, request: RequestIn<H, "grantForget">):
    Promise<AnswerIn<H, "grantForget">>;
  grantForgetRevoked(binding: Binding, request: RequestIn<H, "grantForgetRevoked">):
    Promise<AnswerIn<H, "grantForgetRevoked">>;
  keyMint(binding: Binding, request: RequestIn<H, "keyMint">): Promise<AnswerIn<H, "keyMint">>;
  keyList(binding: Binding): Promise<AnswerIn<H, "keyList">>;
  ownerErase(binding: Binding, request: RequestIn<H, "ownerErase">):
    Promise<AnswerIn<H, "ownerErase">>;
  erasureBegin(binding: Binding, request: RequestIn<H, "erasureBegin">):
    Promise<AnswerIn<H, "erasureBegin">>;
  erasureOutside(binding: Binding): Promise<AnswerIn<H, "erasureOutside">>;
  erasureOutsideDone(binding: Binding): Promise<AnswerIn<H, "erasureOutsideDone">>;
  inboundList(binding: Binding): Promise<AnswerIn<H, "inboundList">>;
  inboundCreate(binding: Binding, request: RequestIn<H, "inboundCreate">):
    Promise<AnswerIn<H, "inboundCreate">>;
  inboundSet(binding: Binding, request: RequestIn<H, "inboundSet">):
    Promise<AnswerIn<H, "inboundSet">>;
  inboundRevoke(binding: Binding, request: RequestIn<H, "inboundRevoke">):
    Promise<AnswerIn<H, "inboundRevoke">>;
  inboundAccept(binding: Binding, request: RequestIn<H, "inboundAccept">):
    Promise<AnswerIn<H, "inboundAccept">>;
  fileUploadTarget(binding: Binding, request: RequestIn<H, "fileUploadTarget">):
    Promise<AnswerIn<H, "fileUploadTarget">>;
  fileUploadStored(binding: Binding, request: RequestIn<H, "fileUploadStored">):
    Promise<AnswerIn<H, "fileUploadStored">>;
  registerAgent(binding: Binding, request: RequestIn<H, "registerAgent">):
    Promise<AnswerIn<H, "registerAgent">>;
  registerProposal(binding: Binding, request: RequestIn<H, "registerProposal">):
    Promise<AnswerIn<H, "registerProposal">>;
  disableAgent(binding: Binding, request: RequestIn<H, "disableAgent">):
    Promise<AnswerIn<H, "disableAgent">>;
  agentDoorbell(binding: Binding, request: RequestIn<H, "agentDoorbell">):
    Promise<AnswerIn<H, "agentDoorbell">>;
  agentDoorbellEnd(binding: Binding, request: RequestIn<H, "agentDoorbellEnd">):
    Promise<AnswerIn<H, "agentDoorbellEnd">>;
  deleteAgent(binding: Binding, request: RequestIn<H, "deleteAgent">):
    Promise<AnswerIn<H, "deleteAgent">>;
  connectionPrepare(binding: Binding, request: RequestIn<H, "connectionPrepare">):
    Promise<AnswerIn<H, "connectionPrepare">>;
  connectionCommit(binding: Binding, request: RequestIn<H, "connectionCommit">):
    Promise<AnswerIn<H, "connectionCommit">>;
  connectionRevoke(binding: Binding, request: RequestIn<H, "connectionRevoke">):
    Promise<AnswerIn<H, "connectionRevoke">>;
  connectionList(binding: Binding): Promise<AnswerIn<H, "connectionList">>;
  flowFinish(binding: Binding, request: RequestIn<H, "flowFinish">):
    Promise<AnswerIn<H, "flowFinish">>;
  noticeList(binding: Binding): Promise<AnswerIn<H, "noticeList">>;
  starterReceipts(binding: Binding): Promise<AnswerIn<H, "starterReceipts">>;
  admissionSet(binding: Binding, request: RequestIn<H, "admissionSet">):
    Promise<AnswerIn<H, "admissionSet">>;
  ownerState(binding: Binding): Promise<AnswerIn<H, "ownerState">>;
  toolProfile(binding: Binding): Promise<AnswerIn<H, "toolProfile">>;
  definitionRead(binding: Binding): Promise<AnswerIn<H, "definitionRead">>;
  mailChain(binding: Binding, request: RequestIn<H, "mailChain">):
    Promise<AnswerIn<H, "mailChain">>;
  wakeEvents(binding: Binding, request: RequestIn<H, "wakeEvents">):
    Promise<AnswerIn<H, "wakeEvents">>;
  wakeSubscribe(binding: Binding, request: RequestIn<H, "wakeSubscribe">):
    Promise<AnswerIn<H, "wakeSubscribe">>;
  wakeUnsubscribe(binding: Binding, request: RequestIn<H, "wakeUnsubscribe">):
    Promise<AnswerIn<H, "wakeUnsubscribe">>;
  fileRead(binding: Binding, request: RequestIn<H, "fileRead">): Promise<AnswerIn<H, "fileRead">>;
  fileReadChunks(binding: Binding, request: RequestIn<H, "fileReadChunks">):
    Promise<AnswerIn<H, "fileReadChunks">>;
  fileUploadBegin(binding: Binding, request: RequestIn<H, "fileUploadBegin">):
    Promise<AnswerIn<H, "fileUploadBegin">>;
  fileUploadChunks(binding: Binding, request: RequestIn<H, "fileUploadChunks">):
    Promise<AnswerIn<H, "fileUploadChunks">>;
  fileUploadCommit(binding: Binding, request: RequestIn<H, "fileUploadCommit">):
    Promise<AnswerIn<H, "fileUploadCommit">>;
  fileUploadAbort(binding: Binding, request: RequestIn<H, "fileUploadAbort">):
    Promise<AnswerIn<H, "fileUploadAbort">>;
  fileRedact(binding: Binding, request: RequestIn<H, "fileRedact">):
    Promise<AnswerIn<H, "fileRedact">>;
  fileHistoryPurge(binding: Binding, request: RequestIn<H, "fileHistoryPurge">):
    Promise<AnswerIn<H, "fileHistoryPurge">>;
  fileChanges(binding: Binding, request: RequestIn<H, "fileChanges">):
    Promise<AnswerIn<H, "fileChanges">>;
  runEvents(binding: Binding, request: RequestIn<H, "runEvents">):
    Promise<AnswerIn<H, "runEvents">>;
  runDecide(binding: Binding, request: RequestIn<H, "runDecide">):
    Promise<AnswerIn<H, "runDecide">>;
  signinStart(binding: Binding, request: RequestIn<H, "signinStart">):
    Promise<AnswerIn<H, "signinStart">>;
  signinEvents(binding: Binding, request: RequestIn<H, "signinEvents">):
    Promise<AnswerIn<H, "signinEvents">>;
  signinCode(binding: Binding, request: RequestIn<H, "signinCode">):
    Promise<AnswerIn<H, "signinCode">>;
  signinCancel(binding: Binding, request: RequestIn<H, "signinCancel">):
    Promise<AnswerIn<H, "signinCancel">>;
  signinList(binding: Binding): Promise<AnswerIn<H, "signinList">>;
  runList(binding: Binding, request: RequestIn<H, "runList">): Promise<AnswerIn<H, "runList">>;
  signinReport(binding: Binding, request: RequestIn<H, "signinReport">):
    Promise<AnswerIn<H, "signinReport">>;
  sandboxRung(): Promise<AnswerIn<H, "sandboxRung">>;
  socketTicket(binding: Binding, request: RequestIn<H, "socketTicket">):
    Promise<AnswerIn<H, "socketTicket">>;
  socketsEnd(binding: Binding): Promise<AnswerIn<H, "socketsEnd">>;
  tierSet(binding: Binding, request: RequestIn<H, "tierSet">): Promise<AnswerIn<H, "tierSet">>;
  tierRead(binding: Binding): Promise<AnswerIn<H, "tierRead">>;
  restoreSource(binding: Binding): Promise<AnswerIn<H, "restoreSource">>;
  restoreBegin(binding: Binding, request: RequestIn<H, "restoreBegin">):
    Promise<AnswerIn<H, "restoreBegin">>;
  restoreStep(binding: Binding): Promise<AnswerIn<H, "restoreStep">>;
  restorePin(binding: Binding, request: RequestIn<H, "restorePin">):
    Promise<AnswerIn<H, "restorePin">>;
  restoreWipe(binding: Binding, request: RequestIn<H, "restoreWipe">):
    Promise<AnswerIn<H, "restoreWipe">>;
  restoreRelease(binding: Binding): Promise<AnswerIn<H, "restoreRelease">>;
  restoreNames(binding: Binding, request: RequestIn<H, "restoreNames">):
    Promise<AnswerIn<H, "restoreNames">>;
  restoreHold(binding: Binding, request: RequestIn<H, "restoreHold">):
    Promise<AnswerIn<H, "restoreHold">>;
  egressOpen(binding: Binding, request: RequestIn<H, "egressOpen">):
    Promise<AnswerIn<H, "egressOpen">>;
  egressReserve(binding: Binding, request: RequestIn<H, "egressReserve">):
    Promise<AnswerIn<H, "egressReserve">>;
  egressBeat(binding: Binding, request: RequestIn<H, "egressBeat">):
    Promise<AnswerIn<H, "egressBeat">>;
  egressSettle(binding: Binding, request: RequestIn<H, "egressSettle">):
    Promise<AnswerIn<H, "egressSettle">>;
  egressReceipts(binding: Binding): Promise<AnswerIn<H, "egressReceipts">>;
  fileSyncCommit(binding: Binding, request: RequestIn<H, "fileSyncCommit">):
    Promise<AnswerIn<H, "fileSyncCommit">>;
  fileSyncUploadBegin(binding: Binding, request: RequestIn<H, "fileSyncUploadBegin">):
    Promise<AnswerIn<H, "fileSyncUploadBegin">>;
  fileSyncUploadChunks(binding: Binding, request: RequestIn<H, "fileSyncUploadChunks">):
    Promise<AnswerIn<H, "fileSyncUploadChunks">>;
  fileSyncUploadAbort(binding: Binding, request: RequestIn<H, "fileSyncUploadAbort">):
    Promise<AnswerIn<H, "fileSyncUploadAbort">>;
  importRows(binding: Binding, request: RequestIn<H, "importRows">):
    Promise<AnswerIn<H, "importRows">>;
  importFiles(binding: Binding, request: RequestIn<H, "importFiles">):
    Promise<AnswerIn<H, "importFiles">>;
  importFileBegin(binding: Binding, request: RequestIn<H, "importFileBegin">):
    Promise<AnswerIn<H, "importFileBegin">>;
  importFileStored(binding: Binding, request: RequestIn<H, "importFileStored">):
    Promise<AnswerIn<H, "importFileStored">>;
  importFileCommit(binding: Binding, request: RequestIn<H, "importFileCommit">):
    Promise<AnswerIn<H, "importFileCommit">>;
  importNotice(binding: Binding, request: RequestIn<H, "importNotice">):
    Promise<AnswerIn<H, "importNotice">>;
  importCheck(binding: Binding, request: RequestIn<H, "importCheck">):
    Promise<AnswerIn<H, "importCheck">>;
  importDiscard(binding: Binding): Promise<AnswerIn<H, "importDiscard">>;
}
