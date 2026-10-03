import * as React from "react";
import Link from "next/link";
import { TinyAssetsMark } from "./TinyAssetsMark";
import { SITE } from "../lib/site";
import styles from "./Footer.module.css";

export function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={`container ${styles.grid}`}>
        <div className={styles.brand}>
          <TinyAssetsMark size={26} />
          <p className={styles.line}>
            <strong>TinyAssets</strong> is the platform. <strong>Tiny</strong> is the command center you
            talk to. The code is open source under MIT on{" "}
            <a href={SITE.repo} target="_blank" rel="noreferrer">
              GitHub
            </a>
            .
          </p>
        </div>

        <div className={styles.col}>
          <span className="eyebrow">Use it</span>
          <ul>
            <li><a href={SITE.app}>Open the app</a></li>
            <li><Link href="/start/#connector">Add it to Claude or ChatGPT</Link></li>
            <li><Link href="/start/#android">Android app</Link></li>
            <li><Link href="/start/">Start from zero</Link></li>
          </ul>
        </div>
        <div className={styles.col}>
          <span className="eyebrow">Read</span>
          <ul>
            <li><Link href="/build/">How a command center builds</Link></li>
            <li><Link href="/commons/">Commons</Link></li>
            <li><Link href="/developers/">Developers</Link></li>
            <li><Link href="/fine-print/">Fine print</Link></li>
          </ul>
        </div>
        <div className={styles.col}>
          <span className="eyebrow">Legal</span>
          <ul>
            <li><Link href="/legal/">Terms and privacy</Link></li>
            <li><Link href="/account/">Your account</Link></li>
            <li><a href={`mailto:${SITE.contact.general}`}>{SITE.contact.general}</a></li>
            <li><a href={`mailto:${SITE.contact.security}`}>{SITE.contact.security}</a></li>
          </ul>
        </div>
      </div>

      <div className={`container ${styles.bottom}`}>
        <span className="ev">© 2026 TinyAssets · MIT code · CC0 public shapes</span>
        <span className="ev">
          <a href={SITE.mcp}>tinyassets.io/mcp</a> is the only public endpoint
        </span>
      </div>
    </footer>
  );
}

export default Footer;
