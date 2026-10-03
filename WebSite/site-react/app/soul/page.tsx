import type { Metadata } from "next";
import { Moved } from "../_components/Moved";

export const metadata: Metadata = {
  title: "Moved",
  robots: { index: false, follow: true },
};

export default function Page() {
  return <Moved to="/build/" name="Build" line="Souls became the brain a command center keeps for itself." />;
}
