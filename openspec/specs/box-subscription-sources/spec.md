# Box subscription sources

## Purpose
Restore native subscription model access inside isolated owner boxes using editable source data.

## Requirements

### Requirement: Broker custody and one-run native leases
Native CLI credential state SHALL live in the authenticated owner's broker custody namespace, as required by ADR-022 P2b and ADR-012 D5. Login, status and invocation SHALL use a private temporary HOME on the box's tmpfs, never persistent HOME. Declared config environment and state paths SHALL resolve under that temporary HOME. Only declared state files SHALL cross the existing bound owner broker channel. No vendor-specific credential parser is required.

#### Scenario: Login capture
- **WHEN** native login succeeds and its status check confirms authentication
- **THEN** declared state is committed to broker custody before the connection is published and temporary state is wiped; persistent owner HOME contains no credential file

#### Scenario: Refresh and concurrent invocation
- **WHEN** two invocations use one source and the first rotates its credential
- **THEN** the second waits for the source lease and receives the refreshed state; writeback uses the broker generation and stale generations cannot overwrite or recreate custody

#### Scenario: Status and failed runs
- **WHEN** authentication is checked or a CLI exits with an error after refreshing
- **THEN** custody presence and a leased native check determine authentication; updated declared state is written back under the generation check and temporary files are wiped even on failure

#### Scenario: Disconnect during login
- **WHEN** the owner disconnects a source with a pending login
- **THEN** pending login is cancelled first, its process exits before erasure completes, custody and connection are deleted, and late writeback cannot resurrect either

#### Scenario: Existing persistent login
- **WHEN** first use finds legacy declared state files under persistent HOME
- **THEN** a one-shot migration captures them into custody before deleting them, preserves existing newer custody, and requires no new sign-in; this migration is deleted with cutover code

#### Scenario: Account erasure and platform recovery
- **WHEN** an account is erased or platform disaster recovery resets credentials
- **THEN** native custody is erased alongside HTTP credentials without opening owner files

### Requirement: Owner-box subscription runtime
The system SHALL load editable source definitions and build declared pinned CLI packages once into the shared read-only box image, with the same lock/integrity verification and exact registry egress as runtime installation. Only packages and credential-free configuration live in the owner home; native login state follows the broker custody and one-run lease requirement. A record absent from the image MAY install into the admitted owner volume with a 1 GiB free-space check and failure/cache cleanup.

#### Scenario: First installation and reuse
- **WHEN** an owner connects a source twice
- **THEN** the matching verified image binary is reused and no owner install is performed

#### Scenario: Fallback installation
- **WHEN** a user-added source needs installation
- **THEN** connect returns immediately, the conversation shows it as installing, and offers sign-in when ready without a waiting tab

### Requirement: Native login and durable isolation
The system SHALL run the native CLI login in the box and expose its URL or device code, verifying completion before publishing a source.

#### Scenario: Login across sleep and wake
- **WHEN** a successful login is followed by box shutdown and restart
- **THEN** that owner can invoke the source and another owner cannot see the login

#### Scenario: Pending login interrupted by drain or sleep
- **WHEN** the box drains or goes to sleep while a sign-in is pending
- **THEN** the login cancellation event is set and the login does not count as active work or renew the idle clock
- **AND** a failed poll or cancelled login shows "Sign-in was interrupted. Tap Connect to try again." with Connect enabled

### Requirement: Generic invocation and selection
The system SHALL invoke subscription_cli only through the owner box using definition argv/output data, and include connected sources in normal owner model selection.

#### Scenario: Non-interactive reply
- **WHEN** an authorized owner selects a connected subscription source
- **THEN** a model call returns the CLI response without a provider-specific Python adapter

### Requirement: Definition-generated connect controls
The page SHALL resolve declared address aliases to the same SourceRecord schema used by detected HTTP sources, then offer the native login handoff from the single address field. There SHALL be no separate subscription source list.

#### Scenario: Native code handoff
- **WHEN** the CLI requires a returned code because its callback cannot be reached
- **THEN** the page relays the code to the same owner-bound login process and does not report success before verification

### Requirement: Evidence-based default selection
The system SHALL include connected subscription sources in ordinary automatic model ordering, using only declared comparable evidence with a bounded freshness period and preserving explicit owner preferences.

#### Scenario: Stronger source connected
- **WHEN** a newly connected source has stronger fresh comparable evidence than the current automatic choice
- **THEN** automatic ordering selects it without replacing an explicit saved preference

#### Scenario: Unknown native default
- **WHEN** the native default alias has no comparable model evidence
- **THEN** it remains unranked rather than receiving an invented vendor quality score

### Requirement: Disconnect connected sources
The connect dialog SHALL list connected sources first and offer Disconnect. HTTP sources SHALL use the existing connections disconnect endpoint. Subscription disconnect SHALL remove the record's declared owner-local state paths, connection marker and owner-local install directory (`home/.ta-cli/<id>`), when present. It SHALL also remove the owner's `subscription_cli` provider definitions for that source in every active command center, and withdraw the broker-held source descriptors they published, so no unservable source survives. Removal SHALL be scoped to the disconnecting owner. The shared read-only image installation SHALL never be touched.

#### Scenario: Disconnect removes owner-local installation
- **WHEN** an owner disconnects a subscription source
- **THEN** its login, connection and owner-local installation are removed while the shared image installation remains available

#### Scenario: Disconnect leaves no definition or descriptor
- **WHEN** an owner disconnects a subscription source they had registered as a provider definition
- **THEN** that definition and its broker-held descriptor are gone, the source is no longer a connected source, and another owner's definition and descriptor for the same source id remain

### Requirement: Record-bound acceptance
A connected subscription marker SHALL bind the full source record fingerprint. A changed or legacy unbound record SHALL require reconnection before discovery, selection or invocation runs its CLI. A malformed record file SHALL not prevent other records loading; malformed overrides SHALL not reactivate a bundled record. Image builds SHALL fail on malformed shipped records.

#### Scenario: Record changes after connection
- **WHEN** a source's package, arguments, egress, state or other record content changes
- **THEN** its previous connection is not accepted for the changed source
