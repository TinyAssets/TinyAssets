import styles from "./Reachability.module.css";

/** Public pages cannot read connector activity. This boundary is known at build time. */
export function Reachability() {
  return (
    <div className={styles.strip}>
      <dl className="receipt" aria-label="Live readings">
        <div>
          <dt>live readings</dt>
          <dd>sign-in required</dd>
        </div>
      </dl>
      <div className={styles.bar}>
        <span className="note">
          Live reachability and activity stay private until a connector supplies a bearer.
        </span>
      </div>
    </div>
  );
}

export default Reachability;
