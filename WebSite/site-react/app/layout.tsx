// Root layout — the shell. Imports the design system's full style layer first
// (tokens + base + vocabulary + component CSS), then site glue.
import "@tiny/design-system/styles.css";
import "./globals.css";

import type { Metadata } from "next";
import TopNav from "../components/TopNav";
import Footer from "../components/Footer";
import { TINYASSETS_MARK_VERSION } from "../components/TinyAssetsMark";
import { SITE } from "../lib/site";

const TITLE = "TinyAssets — one app, your agents, your model";
const DESCRIPTION =
  "One app on any device. You bring a model you already use. Your agents keep your files and your work, and a setup you publish can be copied. Early access is open. The platform does not supply a model.";
const markAsset = (path: string) => `${path}?v=${TINYASSETS_MARK_VERSION}`;

export const metadata: Metadata = {
  metadataBase: new URL(SITE.origin),
  title: { default: TITLE, template: "%s — TinyAssets" },
  description: DESCRIPTION,
  manifest: markAsset("/site.webmanifest"),
  icons: {
    icon: [
      { url: markAsset("/favicon.ico"), sizes: "16x16 32x32 48x48" },
      { url: markAsset("/icon.svg"), type: "image/svg+xml" },
    ],
    apple: markAsset("/apple-touch-icon.png"),
  },
  openGraph: {
    siteName: "TinyAssets",
    type: "website",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/og-image.png"],
    url: SITE.origin + "/",
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/og-image.png"],
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE.origin}/#org`,
      name: "TinyAssets",
      url: `${SITE.origin}/`,
      logo: `${SITE.origin}${markAsset("/logo-mark.png")}`,
      sameAs: [SITE.repo],
    },
    {
      "@type": "WebSite",
      "@id": `${SITE.origin}/#site`,
      url: `${SITE.origin}/`,
      name: "TinyAssets",
      description: DESCRIPTION,
      publisher: { "@id": `${SITE.origin}/#org` },
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${SITE.origin}/#app`,
      name: "TinyAssets",
      applicationCategory: "ProductivityApplication",
      operatingSystem: "Web, Android, Windows, macOS, Linux",
      url: SITE.app,
      license: `${SITE.repo}/blob/main/LICENSE`,
    },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
        <a className="skip" href="#main">
          Skip to content
        </a>
        <TopNav />
        <main id="main">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
