package io.tinyassets.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.RemoteInput;

import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.security.SecureRandom;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Turns the server's data-only FCM message into the "Waiting on you"
 * notification, and takes it down again on a silent clear.
 *
 * The server sends DATA ONLY, because a message with a notification block is
 * drawn by the system without ever reaching app code, and the system cannot
 * attach an inline Reply. Everything shown is server-composed: title and body
 * arrive as data, the ids arrive as data, and nothing here reads a URL, a
 * credential or a destination from the message.
 *
 * Tapping opens the app at /app?request=<id>[&item=<id>]. The Reply action's
 * text is handed to the app and submitted there through its own signed-in
 * session -- no credential is ever attached to the notification. Because
 * MainActivity is exported (it is the launcher), a Reply intent carries a
 * per-install secret that only this app's private storage holds; without it the
 * app refuses to treat text as a reply, so another app on the phone cannot
 * make this one answer a request.
 */
public class TinyAssetsMessagingService extends FirebaseMessagingService {
    static final String ACTION_OPEN = "io.tinyassets.app.OPEN_REQUEST";
    static final String ACTION_REPLY = "io.tinyassets.app.REPLY_REQUEST";
    static final String EXTRA_REQUEST_ID = "request_id";
    static final String EXTRA_ITEM_ID = "item_id";
    static final String EXTRA_NONCE = "reply_nonce";
    static final String EXTRA_RECIPIENT = "recipient";
    static final String REPLY_KEY = "reply";
    static final String CHANNEL_ID = "requests";
    static final int NOTIFICATION_ID = 1;

    // Ids are ASCII by the server's own validation; anything else is dropped
    // rather than put in an intent or a URL.
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_:.\\-]{1,120}");
    private static final String PREFS = "tinyassets_notifications";
    private static final String NONCE_KEY = "reply_nonce";
    private static final String ACTIVE_KEY = "push_active";
    private static final String RECIPIENT_KEY = "push_recipient";

    static String safeId(String value) {
        return value != null && ID.matcher(value).matches() ? value : null;
    }

    /** The per-install secret a Reply intent must carry. App-private storage. */
    /**
     * The account this phone is armed for, or null when it is armed for none.
     * The web layer sets it on register (to the opaque recipient tag the
     * server returned for the signed-in owner) and clears it on sign-out,
     * synchronously. Every message carries the tag of the owner it was sent
     * to, and one that does not match is dropped -- so a message arriving after
     * sign-out, or one already in flight when the handset changed hands, is
     * never shown to whoever holds the phone now, however long FCM takes to
     * delete the token.
     */
    static String armedRecipient(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (!prefs.getBoolean(ACTIVE_KEY, false)) return null;
        return prefs.getString(RECIPIENT_KEY, null);
    }

    // Held while a message is checked AND posted, and while the phone is armed
    // or disarmed. FCM delivers on a worker thread: without this, a message
    // could pass the check, sign-out could disarm and clear the tray, and the
    // worker would then post the previous owner's text onto the next owner's
    // screen. With it, the post either happens before the disarm (and the
    // page's clear that follows removes it) or does not happen at all.
    private static final Object ARM_LOCK = new Object();

    static void setActive(Context context, boolean active, String recipient) {
        synchronized (ARM_LOCK) {
            writeActive(context, active, recipient);
        }
    }

    private static void writeActive(Context context, boolean active, String recipient) {
        SharedPreferences.Editor edit = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit();
        if (active && safeId(recipient) != null) {
            edit.putBoolean(ACTIVE_KEY, true).putString(RECIPIENT_KEY, recipient);
        } else {
            edit.putBoolean(ACTIVE_KEY, false).remove(RECIPIENT_KEY);
        }
        edit.apply();
    }

    static synchronized String replyNonce(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String nonce = prefs.getString(NONCE_KEY, null);
        if (nonce == null || nonce.length() < 32) {
            byte[] raw = new byte[24];
            new SecureRandom().nextBytes(raw);
            StringBuilder hex = new StringBuilder();
            for (byte b : raw) hex.append(String.format("%02x", b));
            nonce = hex.toString();
            prefs.edit().putString(NONCE_KEY, nonce).apply();
        }
        return nonce;
    }

    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        // The web layer re-registers on every launch as well; this covers a
        // refresh that happens while the app is open.
        PushNotificationsPlugin.onNewToken(token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Map<String, String> data = message.getData();
        String requestId = safeId(data.get("request_id"));
        if (requestId == null) return;
        NotificationManager manager =
            (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        String recipient = data.get("recipient");
        if (recipient == null) return;
        synchronized (ARM_LOCK) {
            String armed = armedRecipient(this);
            // A message for anyone but the account this phone is armed for is
            // dropped -- a clear included: request ids repeat across owners, so
            // a clear meant for someone else must not cancel this owner's.
            if (armed == null || !armed.equals(recipient)) return;
            if ("clear".equals(data.get("kind"))) {
                manager.cancel(requestId, NOTIFICATION_ID);
                return;
            }
            show(manager, requestId, recipient, data);
        }
    }

    private void show(NotificationManager manager, String requestId, String recipient,
                      Map<String, String> data) {
        String title = data.get("title");
        String body = data.get("body");
        if (title == null || body == null) return;
        ensureChannel(manager);
        String itemId = safeId(firstItem(data.get("item_ids")));

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setContentIntent(pending(requestId, itemId, ACTION_OPEN, false, recipient));

        RemoteInput reply = new RemoteInput.Builder(REPLY_KEY).setLabel("Reply").build();
        builder.addAction(new NotificationCompat.Action.Builder(
            android.R.drawable.ic_menu_send, "Reply",
            pending(requestId, itemId, ACTION_REPLY, true, recipient))
            .addRemoteInput(reply)
            .setShowsUserInterface(true)
            .build());
        manager.notify(requestId, NOTIFICATION_ID, builder.build());
    }

    /** A notification is for the request. Per-item answers happen in the app. */
    private static String firstItem(String itemIds) {
        if (itemIds == null) return null;
        // A request that carries several items opens the request, not one item.
        return itemIds.contains(",") ? null : itemIds;
    }

    private PendingIntent pending(String requestId, String itemId, String action, boolean reply,
                                  String recipient) {
        Intent intent = new Intent(this, MainActivity.class)
            .setAction(action)
            // Distinguishes the two PendingIntents for one request: extras are
            // not part of Intent equality, so without a distinct data URI the
            // second would silently replace the first.
            .setData(Uri.parse("tinyassets-notification://"
                + action.substring(action.lastIndexOf('.') + 1)
                + "/" + Uri.encode(requestId)))
            .putExtra(EXTRA_REQUEST_ID, requestId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (itemId != null) intent.putExtra(EXTRA_ITEM_ID, itemId);
        if (reply) {
            intent.putExtra(EXTRA_NONCE, replyNonce(this));
            // The tag of the MESSAGE this notification was built from, not
            // whatever the phone is armed for when this line runs.
            intent.putExtra(EXTRA_RECIPIENT, recipient);
        }
        // A RemoteInput reply is written into the PendingIntent by the system,
        // so it must be MUTABLE: explicitly from Android 12, and by default
        // before it (where FLAG_IMMUTABLE would stop the reply text arriving).
        // The plain tap stays immutable.
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (!reply) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            flags |= PendingIntent.FLAG_MUTABLE;
        }
        return PendingIntent.getActivity(this, (action + requestId).hashCode(), intent, flags);
    }

    private void ensureChannel(NotificationManager manager) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        // Always (re)create: for an existing channel id Android applies the new
        // name and description and keeps the person's own importance choice, so
        // copy changes reach installed apps.
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID, "Waiting on you", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("Requests your agent is waiting on you for");
        manager.createNotificationChannel(channel);
    }
}
