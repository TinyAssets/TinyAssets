"""Finish the generated Android project so in-app OAuth works.

`npx cap add android` generates android/ (gitignored); this runs right after it
in CI (and locally) to:

1. register the tinyassets://auth deep link (intent-filter on MainActivity) so
   the in-app OAuth tab can hand the sign-in code back to the app;
2. install the LocalCallback plugin (mobile/native/android/LocalCallbackPlugin.java)
   and register it in MainActivity, so the app can catch a provider's
   http://localhost:PORT/auth/callback redirect — the same browser sign-in a
   desktop CLI completes, with no per-account "device code" setting needed;
3. give the back gesture a policy (MainActivity.installBackPolicy), because the
   plugin default swallows it on the opening screen.

Idempotent.
"""

from __future__ import annotations

import pathlib
import re
import sys

MOBILE = pathlib.Path(__file__).resolve().parents[1]
ANDROID = MOBILE / "android"
MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"
JAVA_PKG_DIR = ANDROID / "app/src/main/java/io/tinyassets/app"
MAIN_ACTIVITY = JAVA_PKG_DIR / "MainActivity.java"
PLUGIN_SRC = MOBILE / "native/android/LocalCallbackPlugin.java"
PLUGIN_DST = JAVA_PKG_DIR / "LocalCallbackPlugin.java"
SERVICE_SRC = MOBILE / "native/android/LocalCallbackService.java"
SERVICE_DST = JAVA_PKG_DIR / "LocalCallbackService.java"
VOICE_CHROME_SRC = MOBILE / "native/android/VoiceWebChromeClient.java"
VOICE_CHROME_DST = JAVA_PKG_DIR / "VoiceWebChromeClient.java"
# Native request notifications (FCM): the message service that draws the
# notification and the plugin that hands an inline Reply's text to the page.
NOTIFY_SOURCES = (
    "TinyAssetsMessagingService.java",
    "NotificationReplyPlugin.java",
)
APP_BUILD_GRADLE = ANDROID / "app/build.gradle"
# Same default @capacitor/push-notifications declares for itself. The app module
# compiles its own FirebaseMessagingService subclass, and the plugin's
# `implementation` dependency is not visible to it.
FIREBASE_MESSAGING = "com.google.firebase:firebase-messaging:25.0.1"
PLUGIN_SERVICE = "com.capacitorjs.plugins.pushnotifications.MessagingService"

# The loopback listener's keep-alive: a dataSync foreground service. Android 14+
# requires the type in the manifest, and Play requires a matching Console
# declaration plus behavior video. Stopped when the user flow ends.
SERVICE_XML = """        <service
            android:name=".LocalCallbackService"
            android:exported="false"
            android:foregroundServiceType="dataSync" />
"""
# Replaces the push plugin's own service (removed via tools:node="remove") so the
# server's data-only message is drawn by app code, with an inline Reply.
NOTIFY_SERVICE_XML = """        <service
            android:name=".TinyAssetsMessagingService"
            android:exported="false">
            <intent-filter>
                <action android:name="com.google.firebase.MESSAGING_EVENT" />
            </intent-filter>
        </service>
        <service
            android:name="com.capacitorjs.plugins.pushnotifications.MessagingService"
            tools:node="remove" />
"""
REQUIRED_PERMISSIONS = (
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.RECORD_AUDIO",
)

FILTER = """            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="tinyassets" android:host="auth" />
            </intent-filter>
            <!-- Share-to-app fallback for the OpenAI sign-in: if the provider's
                 localhost redirect cannot reach the app (the phone froze it),
                 the stuck browser page still has the callback URL in its address
                 bar; the tab's Share button sends it here and MainActivity turns
                 it into the tinyassets://auth deep link. -->
            <intent-filter android:label="Finish sign-in in TinyAssets">
                <action android:name="android.intent.action.SEND" />
                <category android:name="android.intent.category.DEFAULT" />
                <data android:mimeType="text/plain" />
            </intent-filter>
"""

MAIN_ACTIVITY_SRC = r"""package io.tinyassets.app;

import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;
import android.webkit.WebView;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.core.app.RemoteInput;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.WebViewListener;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends BridgeActivity {
    private VoiceWebChromeClient voiceChromeClient;

    // A shared OAuth callback URL (from the browser tab's Share button after a
    // failed localhost redirect). Only the loopback callback shape is accepted;
    // its query (code + state) is re-issued as the app's own deep link, which
    // the web layer verifies against the pending flow's state before use.
    private static final Pattern CALLBACK = Pattern.compile(
        "https?://(?:localhost|127\\.0\\.0\\.1)(?::\\d+)?/auth/callback\\?([^\\s]+)");

    private static Intent rewriteSharedCallback(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return intent;
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null) return intent;
        Matcher m = CALLBACK.matcher(text);
        if (!m.find()) return intent;
        String query = m.group(1);
        int hash = query.indexOf('#');
        if (hash >= 0) query = query.substring(0, hash);
        Uri deep = Uri.parse("tinyassets://auth?provider=openai&" + query);
        return new Intent(Intent.ACTION_VIEW, deep);
    }

    // A notification tap or inline Reply (see TinyAssetsMessagingService) opens
    // the app at /app?request=<id>[&item=<id>]. Cold start: the bridge is not up
    // yet, so the target waits here until the first page has loaded.
    private static final String APP_URL = "https://tinyassets.io/app";
    private String pendingNotificationUrl;

    /**
     * The in-app URL a notification intent points at, or null when the intent is
     * not one of ours. Ids are re-validated (this activity is exported, so any
     * app can send it an intent). A Reply additionally must carry the
     * per-install secret that only this app's private storage holds; its text is
     * parked for the page to submit through its own session, and the
     * notification is taken down because the reply is now the app's to deliver.
     */
    private String notificationTarget(Intent intent) {
        if (intent == null) return null;
        String action = intent.getAction();
        boolean reply = TinyAssetsMessagingService.ACTION_REPLY.equals(action);
        if (!reply && !TinyAssetsMessagingService.ACTION_OPEN.equals(action)) return null;
        String requestId = TinyAssetsMessagingService.safeId(
            intent.getStringExtra(TinyAssetsMessagingService.EXTRA_REQUEST_ID));
        if (requestId == null) return null;
        String itemId = TinyAssetsMessagingService.safeId(
            intent.getStringExtra(TinyAssetsMessagingService.EXTRA_ITEM_ID));
        if (reply) {
            String nonce = intent.getStringExtra(TinyAssetsMessagingService.EXTRA_NONCE);
            Bundle results = RemoteInput.getResultsFromIntent(intent);
            CharSequence text = results == null
                ? null : results.getCharSequence(TinyAssetsMessagingService.REPLY_KEY);
            String expected = TinyAssetsMessagingService.replyNonce(this);
            if (nonce == null || !MessageDigest.isEqual(
                    nonce.getBytes(StandardCharsets.UTF_8),
                    expected.getBytes(StandardCharsets.UTF_8))) {
                return null;
            }
            if (text == null || text.toString().trim().isEmpty()) return null;
            // The reply belongs to the account the notification was for. If the
            // phone is armed for a different one (or none) -- sign-out, an
            // account switch -- the text is dropped, never re-homed.
            String recipient = intent.getStringExtra(TinyAssetsMessagingService.EXTRA_RECIPIENT);
            String armed = TinyAssetsMessagingService.armedRecipient(this);
            if (recipient == null || armed == null || !armed.equals(recipient)) return null;
            NotificationReplyPlugin.park(requestId, itemId, text.toString(), recipient);
            NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.cancel(requestId, TinyAssetsMessagingService.NOTIFICATION_ID);
            }
        }
        // Handled once: a recreated activity must not replay the reply.
        intent.setAction(Intent.ACTION_MAIN);
        return APP_URL + "?request=" + Uri.encode(requestId)
            + (itemId != null ? "&item=" + Uri.encode(itemId) : "");
    }

    // The back gesture. @capacitor/app installs an always-enabled
    // OnBackPressedCallback that walks WebView history and, at the first entry,
    // does NOTHING -- so on the opening screen the gesture is swallowed and the
    // app cannot be left by going back at all. This callback is added after the
    // bridge is built, and the dispatcher calls the most recently added enabled
    // callback first, so ours decides: walk history while there is history, then
    // ask once before leaving. The confirmation is the point -- an edge-swipe is
    // easy to trigger by accident while typing, and the thing behind it is a
    // conversation in progress.
    private static final long EXIT_CONFIRM_WINDOW_MS = 2500L;
    private long exitConfirmAt;

    private void installBackPolicy() {
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                WebView webView = bridge == null ? null : bridge.getWebView();
                if (webView != null && webView.canGoBack()) {
                    exitConfirmAt = 0L;
                    webView.goBack();
                    return;
                }
                long now = SystemClock.elapsedRealtime();
                if (exitConfirmAt != 0L && now - exitConfirmAt <= EXIT_CONFIRM_WINDOW_MS) {
                    exitConfirmAt = 0L;
                    // Leave the way Home does, without tearing down the
                    // signed-in WebView: coming back resumes the conversation
                    // instead of reloading the app over the network.
                    moveTaskToBack(true);
                    return;
                }
                exitConfirmAt = now;
                Toast.makeText(
                    MainActivity.this, "Press back again to leave TinyAssets", Toast.LENGTH_SHORT
                ).show();
            }
        });
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Local (non-npm) plugins: the loopback OAuth catcher and the inline
        // Reply hand-off. Must be registered before super.onCreate() loads the
        // bridge.
        registerPlugin(LocalCallbackPlugin.class);
        registerPlugin(NotificationReplyPlugin.class);
        bridgeBuilder.addWebViewListener(new WebViewListener() {
            @Override
            public void onPageLoaded(WebView webView) {
                VoiceWebChromeClient.installMediaTracker(webView);
                String target = pendingNotificationUrl;
                if (target != null) {
                    pendingNotificationUrl = null;
                    webView.loadUrl(target);
                }
            }
        });
        setIntent(rewriteSharedCallback(getIntent()));
        pendingNotificationUrl = notificationTarget(getIntent());
        super.onCreate(savedInstanceState);
        if (bridge != null) {
            voiceChromeClient = new VoiceWebChromeClient(bridge, this);
            bridge.getWebView().setWebChromeClient(voiceChromeClient);
        }
        installBackPolicy();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(rewriteSharedCallback(intent));
        String target = notificationTarget(intent);
        if (target != null && bridge != null) {
            bridge.getWebView().loadUrl(target);
        }
    }

    @Override
    public void onPause() {
        if (voiceChromeClient != null && bridge != null) {
            voiceChromeClient.stopCapture(bridge.getWebView());
        }
        super.onPause();
    }

    @Override
    public void onStop() {
        if (voiceChromeClient != null && bridge != null) {
            voiceChromeClient.stopCaptureAndDeny(bridge.getWebView());
        }
        super.onStop();
    }

    @Override
    public void onDestroy() {
        if (voiceChromeClient != null && bridge != null) {
            voiceChromeClient.stopCaptureAndDeny(bridge.getWebView());
        }
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        boolean handled = voiceChromeClient != null
            && voiceChromeClient.onRequestPermissionsResult(requestCode, permissions, results);
        if (handled) {
            return;
        }
        super.onRequestPermissionsResult(requestCode, permissions, results);
    }
}
"""


def register_scheme() -> int:
    """Add each intent-filter independently, so a generated project patched by
    an OLDER version of this script still gains the newer filters."""
    text = MANIFEST.read_text(encoding="utf-8")
    marker = "</activity>"
    if marker not in text:
        print("no </activity> in manifest", file=sys.stderr)
        return 1
    # Capacitor's MainActivity is the first (and only) <activity>; launchMode is
    # singleTask in the template, so the VIEW intent reaches the running app via
    # onNewIntent -> Capacitor App plugin `appUrlOpen`.
    view_filter, send_filter = FILTER.split("            <!--", 1)
    send_filter = "            <!--" + send_filter
    added = []
    if 'android:scheme="tinyassets"' not in text:
        text = text.replace(marker, view_filter + "        " + marker, 1)
        added.append("tinyassets://auth")
    if 'android:label="Finish sign-in in TinyAssets"' not in text:
        text = text.replace(marker, send_filter + "        " + marker, 1)
        added.append("share-to-app")
    if added:
        MANIFEST.write_text(text, encoding="utf-8")
        print("registered intent-filters: " + ", ".join(added))
    else:
        print("intent-filters already registered")
    return 0


def harden_application() -> int:
    """Keep authenticated WebView state out of backups and reject cleartext."""
    text = MANIFEST.read_text(encoding="utf-8")
    matches = list(re.finditer(r"<application\b[^>]*>", text, re.DOTALL))
    if len(matches) != 1:
        print(f"expected one <application> tag, found {len(matches)}", file=sys.stderr)
        return 1
    match = matches[0]
    tag = match.group(0)
    for attr, value in (
        ("android:allowBackup", "false"),
        ("android:usesCleartextTraffic", "false"),
    ):
        pattern = rf'{re.escape(attr)}="[^"]*"'
        if re.search(pattern, tag):
            tag = re.sub(pattern, f'{attr}="{value}"', tag, count=1)
        else:
            tag = tag[:-1] + f'\n        {attr}="{value}">'
    updated = text[: match.start()] + tag + text[match.end() :]
    if updated != text:
        MANIFEST.write_text(updated, encoding="utf-8")
        print("disabled Android backup + cleartext traffic")
    else:
        print("Android backup + cleartext hardening already current")
    return 0


def register_service() -> int:
    """Declare the keep-alive foreground service + its permissions."""
    text = MANIFEST.read_text(encoding="utf-8")
    changed = False
    if 'android:name=".LocalCallbackService"' not in text:
        marker = "</application>"
        if marker not in text:
            print("no </application> in manifest", file=sys.stderr)
            return 1
        text = text.replace(marker, SERVICE_XML + "    " + marker, 1)
        changed = True
    marker = "</manifest>"
    if marker not in text:
        print("no </manifest> in manifest", file=sys.stderr)
        return 1
    for permission in REQUIRED_PERMISSIONS:
        if f'android:name="{permission}"' not in text:
            declaration = f'    <uses-permission android:name="{permission}" />\n'
            text = text.replace(marker, declaration + marker, 1)
            changed = True
    if changed:
        MANIFEST.write_text(text, encoding="utf-8")
        print("registered LocalCallbackService + foreground-service permissions")
    else:
        print("service already registered")
    return 0


def register_notifications() -> int:
    """Swap in our FCM message service and give the app module Firebase."""
    text = MANIFEST.read_text(encoding="utf-8")
    changed = False
    tools_ns = 'xmlns:tools="http://schemas.android.com/tools"'
    if tools_ns not in text:
        match = re.search(r"<manifest\b", text)
        if not match:
            print("no <manifest> in manifest", file=sys.stderr)
            return 1
        text = text[: match.end()] + " " + tools_ns + text[match.end():]
        changed = True
    if 'android:name=".TinyAssetsMessagingService"' not in text:
        marker = "</application>"
        if marker not in text:
            print("no </application> in manifest", file=sys.stderr)
            return 1
        text = text.replace(marker, NOTIFY_SERVICE_XML + "    " + marker, 1)
        changed = True
    if changed:
        MANIFEST.write_text(text, encoding="utf-8")
        print("registered TinyAssetsMessagingService (push plugin service removed)")
    else:
        print("notification service already registered")

    gradle = APP_BUILD_GRADLE.read_text(encoding="utf-8")
    if FIREBASE_MESSAGING in gradle:
        print("firebase-messaging dependency already present")
        return 0
    updated, count = re.subn(
        r"(?m)^dependencies\s*\{[ \t]*$",
        lambda m: m.group(0) + f'\n    implementation "{FIREBASE_MESSAGING}"',
        gradle,
        count=1,
    )
    if count != 1:
        print("no top-level dependencies block in app/build.gradle", file=sys.stderr)
        return 1
    APP_BUILD_GRADLE.write_text(updated, encoding="utf-8")
    print("added firebase-messaging to the app module")
    return 0


def install_plugin() -> int:
    for src in (PLUGIN_SRC, SERVICE_SRC, VOICE_CHROME_SRC) + tuple(
        MOBILE / "native/android" / name for name in NOTIFY_SOURCES
    ):
        if not src.exists():
            print(f"plugin source missing: {src}", file=sys.stderr)
            return 1
    JAVA_PKG_DIR.mkdir(parents=True, exist_ok=True)
    # Preserve bytes so the release verifier's source hash stays stable on both
    # Windows and Linux regardless of newline translation.
    PLUGIN_DST.write_bytes(PLUGIN_SRC.read_bytes())
    SERVICE_DST.write_bytes(SERVICE_SRC.read_bytes())
    VOICE_CHROME_DST.write_bytes(VOICE_CHROME_SRC.read_bytes())
    for name in NOTIFY_SOURCES:
        (JAVA_PKG_DIR / name).write_bytes((MOBILE / "native/android" / name).read_bytes())
    current = MAIN_ACTIVITY.read_text(encoding="utf-8") if MAIN_ACTIVITY.exists() else ""
    if current == MAIN_ACTIVITY_SRC:
        print("MainActivity already current")
        return 0
    # Replace ONLY a recognized MainActivity: Capacitor's untouched template
    # (an empty BridgeActivity subclass) or a version this script wrote earlier
    # (carries its plugin registration). Anything hand-customized is left alone.
    template = bool(
        re.fullmatch(
            r"\s*package io\.tinyassets\.app;\s*import com\.getcapacitor\.BridgeActivity;\s*"
            r"public class MainActivity extends BridgeActivity\s*\{\s*\}\s*",
            current,
        )
    )
    ours = "registerPlugin(LocalCallbackPlugin.class)" in current
    if current and not (template or ours):
        print("customized MainActivity; refusing to overwrite", file=sys.stderr)
        return 1
    MAIN_ACTIVITY.write_text(MAIN_ACTIVITY_SRC, encoding="utf-8")
    print("installed LocalCallback plugin + MainActivity (plugin registration, share rewrite)")
    return 0


def main() -> int:
    if not MANIFEST.exists():
        print(f"manifest not found: {MANIFEST} (run `npx cap add android` first)", file=sys.stderr)
        return 1
    rc = harden_application()
    if rc:
        return rc
    rc = register_scheme()
    if rc:
        return rc
    rc = register_service()
    if rc:
        return rc
    rc = register_notifications()
    if rc:
        return rc
    return install_plugin()


if __name__ == "__main__":
    raise SystemExit(main())
