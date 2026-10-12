## ADDED Requirements

### Requirement: A command center is an app with one manifest
The platform SHALL represent each command center the authenticated account holds as one app. The app's identity SHALL be that command center's id. The manifest SHALL carry the learned name (empty when no name has been learned), the stored description (empty when none is stored), kind `command_center`, the default screen, the holding account as both owner and author, and the UI revision as the version. Reading the manifest SHALL NOT write the command center, its screen, its name, or its owner.

#### Scenario: A command center with a name and a chosen screen
- **WHEN** an account holds a command center named Garden whose chosen screen can render
- **THEN** the app id is that command center's id, the manifest name is Garden, the kind is `command_center`, the default screen is the chosen screen, the owner and author are that account, and the version is the UI revision
- **AND** the stored name, screen, selection and owner row are unchanged

#### Scenario: A command center with no chosen screen
- **WHEN** an account holds a command center with no UI row, or a chosen screen that is missing or cannot render
- **THEN** the default screen is the platform blank screen and the version is the existing revision, including 0 when no UI row exists
- **AND** a stored selection that cannot render is left in place

#### Scenario: A name that exists only in the command center file
- **WHEN** a command center has no universe-row name and `universe.json` names it
- **THEN** the manifest uses that file's name and description
- **AND** the file bytes are unchanged

### Requirement: The launch list shows only this account's apps
`GET /app/apps` SHALL return the authenticated account's apps and no other account's command center. The account SHALL be the authenticated identity alone. A request with no identity SHALL be refused and SHALL read nothing. While the app is dark the route SHALL be absent.

#### Scenario: Two accounts
- **WHEN** Alice holds one command center and Bob holds another
- **THEN** Alice's list contains only Alice's command center

#### Scenario: A home charged to someone else
- **WHEN** Alice's signed-in home is a command center charged to Bob
- **THEN** Alice's list does not contain it and Bob's owner row is unchanged

#### Scenario: No identity
- **WHEN** `GET /app/apps` carries no authenticated identity
- **THEN** the response is `authentication_required` and no app list is returned

### Requirement: Opening an app shows the signed-in command center's default screen
The Apps surface inside `/app` SHALL list the account's apps and SHALL open the signed-in command center by showing the command center view. That view SHALL keep the screen the app already mounts. Opening any other listed copy SHALL NOT switch the signed-in command center.

#### Scenario: The owner opens the signed-in app
- **WHEN** a signed-in owner opens the app whose id is the signed-in command center
- **THEN** the command center view is shown and the card names that app's default screen

#### Scenario: The owner opens a different copy
- **WHEN** a signed-in owner opens a listed app that is not the signed-in command center
- **THEN** the command center view is not switched and the surface says why
