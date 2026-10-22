## Why
The two connection lanes expose competing source shapes and controls.

## What Changes
Unify both under one SourceRecord and one address field. Declared aliases resolve to their mechanism; other addresses use discovery. Retain exact egress and credential custody.

## Capabilities
### Modified Capabilities
- model-source-discovery: unified records and address-only connection.
- box-subscription-sources: declared source records and address aliases.

## Impact
Router, broker, owner-box harness and page. Owner: WX; branch: conv/source-records. Production is expressly out of scope.
