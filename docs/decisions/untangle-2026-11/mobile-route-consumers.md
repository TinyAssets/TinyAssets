# Installed shell links across the route transfer

D3 §F2 places the new shells after cutover and leaves the store dashboard check
with the founder. Existing installed binaries can still open the transferred
host. Step 19 has two different return paths to account for.

* The generated Android activity handles a notification tap and an inline
  reply. Both open `/app?request=<id>` with an optional `item`. For a reply it
  first checks the armed recipient, parks the text in the native plugin and
  dismisses the notification. The retiring web app reads those query fields,
  collects that recipient's reply and submits it against the original request.
  The parked text lasts only for the process lifetime.
* The iOS system sign-in plugin expects its custom callback scheme. The old
  Android manifest also registers that scheme. The new web client's return
  lives at `/app/signed-in`. The first-party web client, an installed
  shell's return and the identity provider's own callback have distinct
  callers. The contract currently describes the web client and the upstream
  callback. Trace the new native return independently of notification delivery.

On main `f934871ad` and joined `ce85dfe5e`, the real Android project generator
and the iOS plist generator produced nine identical fixture files, stable on
a second invocation. Both registered the custom scheme. The generated Android
activity contained the notification target and pending-reply handoff.

Chromium ran the checked-in owner app from each tree with synthetic signed-in
account answers and an instrumented native reply plugin. Five navigations
per tree covered plain Home, request alone, request plus item, the existing
upgrade query and a history fragment alongside the request. Both notification
queries produced the same Home content and calls as plain Home. The app made
zero native reply reads. The upgrade control displayed its distinct message
and the fragment reached Past runs. The check used a loopback-only network
namespace. It exercised neither a device nor a distribution service.

For the later shell release, determine the disposition of outstanding legacy
request identifiers and parked replies. Preserve their originating recipient
and request, including account switches, or explicitly retire the path with
a truthful outcome. A legacy identifier alone supplies no new owner action
or run identity. Check native sign-in return, notification tap and explicit
reply separately before claiming shell parity. The earlier route match and
successful hosted web sign-in supply different evidence.
