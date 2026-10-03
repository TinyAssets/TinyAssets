import type { Metadata } from "next";
import { Moved } from "../_components/Moved";

export const metadata: Metadata = {
  title: "Moved",
  robots: { index: false, follow: true },
};

export default function Page() {
  return <Moved to="/start/" name="Start" line="Hosting became starting: your command center runs in the cloud on your subscription." />;
}
