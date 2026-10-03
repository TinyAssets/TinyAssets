## ADDED Requirements

### Requirement: Sign-in source cards
The connect screen SHALL offer installed sources that are completed by signing in (Hugging Face, scope `inference-api`) as one-tap cards that raise the platform's own `connect` ask from installed data only, with the discovery root passed server-side, the token kept as the owner's oauth2 vault bundle, and the owner's explicit confirmation before the source joins an already-powered agent.

#### Scenario: One tap on a powered command center
- **WHEN** the owner taps the Hugging Face card, approves at huggingface.co and returns
- **THEN** the token is deposited for the card's one inference endpoint, no token reaches the app, and a model-access confirmation for the card's declared models waits for the owner

#### Scenario: Agent-raised imitation
- **WHEN** an agent raises a `connect` ask with the card's destination or payload
- **THEN** either no sign-in offer exists and no row is created, or the sign-in completes without the platform's pool confirmation

#### Scenario: Client identity
- **WHEN** no registered client id is configured
- **THEN** the sign-in names the deployment's public Client ID Metadata Document, whose only redirect URI is the fixed generic callback

### Requirement: Daily-cap card
When a turn stops on a source's explicit daily quota, the app SHALL show a dismissable card that says the free requests for today are used, that the agent continues after the reset, and, for a source with installed daily-cap facts, that the stated credit on the user's own account raises that provider's daily limit to the stated number with the money going to that provider, plus a way to connect another AI.

#### Scenario: OpenRouter free daily cap
- **WHEN** the failure names OpenRouter's credit page
- **THEN** the card says 50/day, that $10 credit on the user's own OpenRouter account raises the OpenRouter daily limit to 1,000, that the money goes to OpenRouter and not TinyAssets, and offers Add credit on OpenRouter, Connect another AI and Wait until tomorrow

#### Scenario: Source without installed facts
- **WHEN** the failure names another provider's credit page or none
- **THEN** the card states no limits or prices it does not have
