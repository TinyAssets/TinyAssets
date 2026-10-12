import tokenInfo from "../lib/token-info.json";
import { CopyAddress } from "./CopyAddress";
import styles from "./TokenContracts.module.css";

/**
 * Renders contract addresses from lib/token-info.json only.
 * Do not paste an address into this file.
 */
export function TokenContracts() {
  return (
    <div className={styles.list} aria-label="Reference token contracts">
      {tokenInfo.deploys.map((deploy) => (
        <article key={deploy.address_main} className={styles.card}>
          <header>
            <strong>{deploy.label}</strong>
            <span>{deploy.chain}</span>
            {deploy.legacy ? <span>1:1 migration reference</span> : null}
          </header>
          <p className={styles.row}>
            <code>{deploy.address_main}</code>
            <CopyAddress address={deploy.address_main} label={deploy.label} />
          </p>
          {deploy.legacy ? <p className={styles.note}>legacy address; no TinyAssets action</p> : null}
          <p className={styles.row}>
            <a href={deploy.explorer} target="_blank" rel="noreferrer">
              Explorer
            </a>
          </p>
        </article>
      ))}
    </div>
  );
}
