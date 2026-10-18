import { Suspense } from "react";

import { Planner } from "@/components/Planner";

export default function Home() {
  return (
    // The planner reads the trip from the URL, so it renders in the browser.
    <Suspense fallback={null}>
      <Planner />
    </Suspense>
  );
}
