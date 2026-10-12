import type { Metadata } from "next";
import { RitualLabel } from "@tiny/design-system";
import { TokenContracts } from "../../components/TokenContracts";
import legal from "../../lib/legal-info.json";
import tokenInfo from "../../lib/token-info.json";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Legal",
  description:
    "Privacy policy and terms of service for TinyAssets, a trade name of Simkal Holdings LLC.",
  alternates: { canonical: "https://tinyassets.io/legal" },
};

export default function LegalPage() {
  return (
    <div className={styles.page}>
      <section className="legal">
        <div className="wrap">
          <RitualLabel>· Legal · privacy and terms ·</RitualLabel>
          <h1>Legal.</h1>
          <p className="lead">
            {legal.operator} (&ldquo;we&rdquo;, &ldquo;us&rdquo;). Questions and requests go to{" "}
            <a href={`mailto:${legal.contact}`}>{legal.contact}</a>.
          </p>

          <nav className="toc" aria-label="On this page">
            <span className="toc__label">On this page</span>
            <a href="#privacy">Privacy</a>
            <a href="#terms">Terms</a>
            <a href="#token">Token</a>
            <a href="#license">License</a>
            <a href="#copyright">Copyright</a>
            <a href="#contact">Contact</a>
          </nav>

          <h2 id="privacy">Privacy Policy</h2>
          <p className="blank">Effective {legal.effective_date}</p>
          <p>
            This policy explains what we collect when you use tinyassets.io, the TinyAssets apps, the MCP server at{" "}
            <code>tinyassets.io/mcp</code>, and the TinyAssets API.
          </p>

          <h3>What we collect</h3>
          <ul>
            <li>
              <strong>Account.</strong> Your email address and a user ID from our sign-in provider (WorkOS).
            </li>
            <li>
              <strong>Agent connections.</strong> When you connect an AI agent or chatbot (for example through remote MCP), we store the connection and the OAuth tokens that let it act on your account.
            </li>
            <li>
              <strong>API keys.</strong> If you create a personal API key, we keep a record of it so we can check later requests.
            </li>
            <li>
              <strong>Credentials you give us.</strong> AI provider keys or tokens you choose to add, so your agents can run on your own AI account.
            </li>
            <li>
              <strong>Your content.</strong> Goals, messages, files, and other work you and your agents create.
            </li>
            <li>
              <strong>Billing.</strong> If you buy a paid plan, Stripe processes the payment. We get billing records (such as plan, status, and invoices) but never your full card number.
            </li>
            <li>
              <strong>Logs.</strong> Basic technical data such as IP address, device and browser info, and request logs, used for security and to keep the service running.
            </li>
          </ul>
          <p>We don&apos;t run advertising trackers or sell your data. The site uses no analytics today.</p>

          <h3>How we use it</h3>
          <p>
            We use this information to run the service, sign you in, connect your agents, process payments, keep the service secure, prevent abuse, and talk to you about your account. We don&apos;t use your content to train AI models.
          </p>

          <h3>Who we share it with</h3>
          <p>
            We share it only with providers that help run the service, and only for that purpose. They are WorkOS (sign-in), Stripe (payments), Amazon Web Services (hosting and storage), and the AI provider you connect (Anthropic, OpenAI, OpenRouter, or another you choose). That provider receives what your agents send it, under your agreement with them. We may also disclose information if the law requires it, or as part of a sale or merger of the business. We do not sell or &ldquo;share&rdquo; personal information for advertising.
          </p>

          <h3>Public content</h3>
          <p>
            Nothing is public until you publish it. Anything you publish to the public commons is released under CC0 and stays public.
          </p>

          <h3>Security</h3>
          <p>
            All traffic is encrypted in transit (TLS). Credentials you give us are kept in access-restricted storage used only by the service. No system is perfectly secure. Remove or rotate any key you no longer want us to hold.
          </p>

          <h3>Retention and deletion</h3>
          <p>
            We keep your data while your account is open. You can delete your account at any time in the app: open Account, type DELETE, and choose <strong>Delete my account</strong>. The same steps are written at <a href="/account">tinyassets.io/account</a>. Deletion takes effect immediately. It removes your account, your content, credentials you stored with us, and your connections, and it cancels any paid plan. We still keep a small amount where we need to: invoices Stripe holds for tax and accounting, de-identified security and audit records, a one-way digest of your sign-in id that cannot be turned back into your email, content you already published to the public commons, and backups until they expire on our backup schedule. If a step cannot finish, the app says so instead of reporting a deletion that did not happen, and we finish that step by hand.
          </p>

          <h3>Your rights</h3>
          <p>
            Depending on where you live, including California and the EU or UK, you can ask to access, correct, delete, or export your data, or object to or limit how we use it. Email <a href={`mailto:${legal.contact}`}>{legal.contact}</a>. We respond within 30 days, and we won&apos;t treat you differently for asking. If we deny a request, reply to that decision and we will review it again. If you are in the EU or UK, you can also complain to your local data protection authority. Where those laws apply, we rely on contract (to provide the service), legitimate interests (security and abuse prevention), consent where we ask for it, and legal obligation. Data is processed in the United States.
          </p>

          <h3>Children</h3>
          <p>
            TinyAssets is for people {legal.minimum_age} and older. We don&apos;t knowingly collect personal information from children under 13. If you think a child has given us personal information, email <a href={`mailto:${legal.contact}`}>{legal.contact}</a> and we will delete it.
          </p>

          <h3>Changes</h3>
          <p>
            We post updates on this page with a new effective date, and we notify you of significant changes by email or in the app.
          </p>

          <h2 id="terms">Terms of Service</h2>
          <p className="blank">Effective {legal.effective_date}</p>
          <p>
            These terms are an agreement between you and Simkal Holdings LLC, doing business as TinyAssets (&ldquo;we&rdquo;, &ldquo;us&rdquo;). By using TinyAssets you agree to them. If you don&apos;t agree, don&apos;t use it.
          </p>
          <ol>
            <li>
              <strong>Who can use it.</strong> You must be {legal.minimum_age} or older and able to form a binding contract. If you use TinyAssets for an organization, you agree for that organization.
            </li>
            <li>
              <strong>Your account and your agents.</strong> Keep your sign-in, API keys, and connected agents secure. You are responsible for what happens under your account, including actions your AI agents take through the MCP server or API. Revoke access you no longer want.
            </li>
            <li>
              <strong>Acceptable use.</strong> Don&apos;t break the law or anyone&apos;s rights. Don&apos;t send malware or spam. Don&apos;t try to get into accounts or systems you are not authorized for. Don&apos;t overload or get around rate limits and safety controls. Don&apos;t scrape or resell the service without permission. Don&apos;t use it to harm people. Don&apos;t use it where U.S. sanctions forbid. We may suspend accounts that break these rules.
            </li>
            <li>
              <strong>Your content.</strong> You own what you put in and what your agents produce. You give us permission to store and process it only to run the service. Content you choose to publish to the public commons is released under CC0.
            </li>
            <li>
              <strong>AI output.</strong> AI-generated output can be wrong, incomplete, or unsuitable. Review it before relying on it, especially for legal, medical, financial, or safety decisions. Third-party AI providers you connect are governed by their own terms.
            </li>
            <li>
              <strong>Paid plans.</strong> Paid plans are billed through Stripe. A plan renews automatically until you cancel. Checkout tells you this before you pay: the plan renews, you can cancel online at any time, and we send a reminder before any annual renewal. After you cancel, you keep access until the end of the period you already paid for. Payments are non-refundable except where the law requires a refund. If the price changes, we tell you before the new price applies to you.
            </li>
            <li>
              <strong>Open source.</strong> The TinyAssets software is MIT-licensed. That license governs the code, and these terms govern the hosted service.
            </li>
            <li>
              <strong>Ending use.</strong> You can stop and delete your account at any time. We may suspend or end access if you break these terms, or to protect the service or other people.
            </li>
            <li>
              <strong>Disclaimers.</strong> The service is provided &ldquo;as is&rdquo; without warranties of any kind, to the extent the law allows. We don&apos;t promise it will be uninterrupted or error-free.
            </li>
            <li>
              <strong>Limitation of liability.</strong> To the extent the law allows, we aren&apos;t liable for indirect, incidental, or consequential damages, or for lost data or profits. Our total liability is limited to the greater of what you paid us in the 12 months before the claim or USD 100. Some places don&apos;t allow these limits, so they may not apply to you.
            </li>
            <li>
              <strong>Indemnity.</strong> You will cover our costs from claims caused by your misuse of the service or by your content.
            </li>
            <li>
              <strong>Governing law.</strong> {legal.governing_law} law governs. Disputes go to small claims or the state or federal courts in {legal.governing_law}. There is no mandatory arbitration.
            </li>
            <li>
              <strong>Changes.</strong> We may update these terms. We post the new version with its effective date and tell you about significant changes. Continuing to use the service means you accept the updated terms.
            </li>
            <li>
              <strong>Contact.</strong> <a href={`mailto:${legal.contact}`}>{legal.contact}</a>
            </li>
          </ol>

          <h3 id="token">The token</h3>
          <p>
            <strong>{tokenInfo.display_name}</strong> is the TinyAssets currency token. The ticker is{" "}
            <code>{tokenInfo.ticker}</code>. These contract addresses are the ones in the site record. The same
            list, with a copy button, is on the <a href="/token/">token page</a>.
          </p>
          <TokenContracts />
          <p>
            This is not an investment, and there is no promise of value. You are responsible for your own wallet.
            Availability may vary by jurisdiction.
          </p>

          <h2 id="license">License</h2>
          <ul>
            <li>
              <strong>Platform code</strong> (engine, MCP gateway, tray, connectors):{" "}
              <a href="https://github.com/TinyAssets/TinyAssets/blob/main/LICENSE" target="_blank" rel="noreferrer">
                MIT
              </a>
              . Fork it, run it, sell services on it. Attribution required.
            </li>
            <li>
              <strong>Public goal content</strong> (goals, branches, nodes, prompt templates, gates, wiki pages):{" "}
              <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noreferrer">
                CC0-1.0
              </a>
              . Public domain. Use without permission.
            </li>
            <li>
              <strong>This site</strong>: same MIT for site code, CC0 for site content where original; fonts retain their respective licenses (Fraunces, Source Sans 3, IBM Plex Mono, served via Google Fonts, all SIL Open Font License).
            </li>
          </ul>

          <h2 id="copyright">Copyright notices</h2>
          <p>
            Send copyright complaints to <a href={`mailto:${legal.contact}`}>{legal.contact}</a>.
          </p>

          <h2 id="contact">Contact</h2>
          <p>
            <a href={`mailto:${legal.contact}`}>{legal.contact}</a>
          </p>
        </div>
      </section>
    </div>
  );
}
