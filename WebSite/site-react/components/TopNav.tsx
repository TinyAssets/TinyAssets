"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { TinyAssetsMark } from "./TinyAssetsMark";
import { NAV, SITE } from "../lib/site";
import styles from "./TopNav.module.css";

function isActive(path: string, href: string): boolean {
  const clean = href.replace(/\/$/, "");
  if (clean === "") return path === "/";
  return path === clean || path.startsWith(clean + "/");
}

export function TopNav() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = React.useState(false);
  const close = () => setOpen(false);

  return (
    <header className={styles.top}>
      <div className={`container ${styles.row}`}>
        <Link className={styles.brand} href="/" aria-label="TinyAssets — home" onClick={close}>
          <TinyAssetsMark size={30} />
          <span className={styles.wordmark}>TinyAssets</span>
        </Link>

        <nav className={styles.nav} aria-label="Primary">
          {NAV.map((it) => (
            <Link
              key={it.href}
              href={it.href}
              className={`${styles.item}${isActive(pathname, it.href) ? " " + styles.active : ""}`}
            >
              {it.label}
            </Link>
          ))}
        </nav>

        <a className={`btn btn--primary btn--sm ${styles.cta}`} href={SITE.app}>
          Open the app
        </a>

        <button
          type="button"
          className={`${styles.hamburger}${open ? " " + styles.open : ""}`}
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen((v) => !v)}
        >
          <span />
          <span />
          <span />
        </button>
      </div>

      {open && (
        <div id="mobile-nav" className={styles.drawer}>
          <nav aria-label="Mobile primary" className={`container ${styles.drawerNav}`}>
            <Link href="/" className={styles.drawerItem} onClick={close}>
              Home
            </Link>
            {NAV.map((it) => (
              <Link
                key={it.href}
                href={it.href}
                className={`${styles.drawerItem}${isActive(pathname, it.href) ? " " + styles.active : ""}`}
                onClick={close}
              >
                {it.label}
              </Link>
            ))}
            <a className={`btn btn--primary btn--md ${styles.drawerCta}`} href={SITE.app}>
              Open the app
            </a>
          </nav>
        </div>
      )}
    </header>
  );
}

export default TopNav;
