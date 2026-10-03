import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Account",
  description:
    "Your account lives in the app. How to sign in, how to delete your account and command center yourself, what is removed, and what is kept.",
  alternates: { canonical: `${SITE.origin}/account/` },
  robots: { index: false, follow: false },
};

export default function AccountPage() {
  return (
    <section className="container narrow hero">
      <RitualLabel>Account</RitualLabel>
      <h1>Your account lives in the app.</h1>
      <p className="lead">
        Sign in to the web app with your email. Your command center, its brain, its connections and its
        stored files are all bound to that account. There is no separate website account.
      </p>
      <div className="actions">
        <a className="btn btn--primary btn--lg" href={SITE.app}>
          Open the app
        </a>
      </div>

      <div className="rule" style={{ marginTop: 48 }}>
        <span className="eyebrow">Deleting your account</span>
      </div>
      <p>
        You can delete your account yourself, in any of the apps — web, iOS, Android or desktop. It takes
        effect immediately and cannot be undone.
      </p>
      <ol>
        <li>Open the app and sign in.</li>
        <li>
          Tap <strong>Account</strong> — top right of the chat, or of the Connect screen.
        </li>
        <li>
          Type <strong>DELETE</strong> to confirm, then tap <strong>Delete my account</strong>.
        </li>
      </ol>
      <p>
        <strong>Removed right away:</strong> your command center — its memory and everything it learned,
        your conversation history, files you sent it, automations and drafts you made — any AI
        credential you deposited, connections you added, your access to any other command center, your
        sign-in identity, and any paid plan, cancelled immediately so nothing further is charged.
      </p>
      <p>
        <strong>Kept:</strong> audit records, with the actor replaced by an opaque id and their
        summary, target and payload emptied; commons rows that are not personal data, such as
        published branch definitions and settlement history, with your name removed from them; a
        one-way digest of your sign-in id, kept so a still-valid session on another device cannot
        silently re-create the account you just deleted — it cannot be turned back into your id or
        your email; the invoices our payment processor holds; and server backups until they age out
        on our retention schedule. Nothing that could rebuild your command center is retained.
      </p>
      <p>
        <strong>If a step cannot complete</strong> — the payment processor is unreachable, say — the
        app tells you instead of claiming a deletion that did not happen, we record exactly which
        step is outstanding, and we finish it by hand. Deletion is also refused, with the reason
        shown, while another person&apos;s data or live work is inside your command center; write to us and
        we will sort it out.
      </p>

      <div className="rule" style={{ marginTop: 48 }}>
        <span className="eyebrow">Can&apos;t sign in?</span>
      </div>
      <p>
        Write to <a href={`mailto:${SITE.contact.legal}`}>{SITE.contact.legal}</a> from the address
        you signed in with, subject &ldquo;Delete my account&rdquo;. We confirm it is you, run the
        same deletion within 30 days, and reply when it is done. The same address handles
        data-export requests.
      </p>
    </section>
  );
}
