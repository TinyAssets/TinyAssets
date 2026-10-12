# Testers

TinyAssets is in early access. The supported product is the hosted app.
You bring a model subscription. The platform does not supply a model
([ADR-006](../decisions/ADR-006-the-platform-has-no-llm.md) if you want the
decision; the short version is on
[the fine print](https://tinyassets.io/fine-print/)).

The public start page is <https://tinyassets.io/start/>. It is the longer
version of this page and stays the canonical install list. If this file
and that page disagree, trust the page and open a docs bug.

## Web

1. Open <https://tinyassets.io/app>.
2. Sign in with email.
3. Connect the Claude or ChatGPT subscription you already have.
4. Say what real thing you want finished.

The same command center is reachable from Claude or ChatGPT as a custom
connector. The only public connector URL is <https://tinyassets.io/mcp>.
The start page has the click-path.

## Android closed test

The Android app is a closed test on Google Play, not a public listing.
The opt-in page the site publishes is:

<https://play.google.com/apps/testing/io.tinyassets.app>

Open that link in the Google account you use on the phone. If Play lets
you become a tester, install from Play afterwards so the app can update.

During the closed test only invited accounts can join. Write to
[info@tinyassets.io](mailto:info@tinyassets.io) to be added, and say which
Google account email should be on the tester list. The public start page
names that same address.

What this page does not know, and will not invent: how many testers are
on the list, which build Play is serving today, or when a public listing
opens. Those live in the Play Console.

### Debug APK

Developers can install a separate debug build from
<https://github.com/TinyAssets/TinyAssets/releases/download/android-latest/app-debug.apk>.
It installs as "TinyAssets (debug)" (`io.tinyassets.app.debug`) and does
not receive Play updates. Testers who want the real app should use the
Play test, not this APK. Details: [mobile/README.md](../../mobile/README.md).

## Desktop

The public site says desktop builds are unsigned and come from the
repository, and that there is no installer download yet. Source:
[desktop-app/](../../desktop-app/).

## iOS

The public start page does not publish an iOS install link. This file
does not add one.

## What to expect

- Early access. Screens break. A conversation can fail when the connected
  subscription is out of quota or not connected.
- The fine print is explicit about what does not exist yet, including no
  platform model and no public Play listing yet.
- The user-facing name is command center. Older screens and a lot of the
  code still say universe. Both words mean your agent, your project, and
  your files. See [docs/architecture.md](../architecture.md).

## Report a bug

Open a [bug report](https://github.com/TinyAssets/TinyAssets/issues/new?template=bug_report.yml).
For "I tried it and here is what I think," use
[tester feedback](https://github.com/TinyAssets/TinyAssets/issues/new?template=tester_feedback.yml).
For "I wish it did this," use a
[feature request](https://github.com/TinyAssets/TinyAssets/issues/new?template=feature_request.yml).

Leave passwords, setup tokens, and session cookies out of the issue. If
the problem is a vulnerability, follow [SECURITY.md](../../SECURITY.md)
and do not file it publicly.

The open issue list is mostly automation (failed deploys, watchdog
restarts). A new report can be hard to see. That is a tracker problem,
not a reason to skip the report.
