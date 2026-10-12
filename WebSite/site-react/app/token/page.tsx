import type { Metadata } from "next";
import Link from "next/link";
import { RitualLabel } from "@tiny/design-system";
import { TokenContracts } from "../../components/TokenContracts";
import tokenInfo from "../../lib/token-info.json";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Token",
  description: `Contract addresses for ${tokenInfo.display_name}. Ticker ${tokenInfo.ticker}.`,
  alternates: { canonical: `${SITE.origin}/token/` },
};

export default function TokenPage() {
  return (
    <section className="container narrow hero">
      <RitualLabel>Token</RitualLabel>
      <h1>{tokenInfo.display_name}</h1>
      <p className="lead">
        {tokenInfo.display_name} is the TinyAssets currency token. The ticker is{" "}
        <code>{tokenInfo.ticker}</code>. The contract addresses below come from the site record. Copy an
        address here, or read the same list in the <Link href="/legal/#token">terms</Link>.
      </p>
      <TokenContracts />
      <p>
        This is not an investment, and there is no promise of value. You are responsible for your own
        wallet. Availability may vary by jurisdiction.
      </p>
    </section>
  );
}
