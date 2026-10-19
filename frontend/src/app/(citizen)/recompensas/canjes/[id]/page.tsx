"use client";
import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { VoucherEntry } from "@/features/rewards/VoucherEntry";
function Content() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  return (
    <VoucherEntry
      id={typeof params.id === "string" ? params.id : ""}
      existing={search.get("existing") === "1"}
    />
  );
}
export default function Page() {
  return (
    <Suspense fallback={<p role="status">Cargando contexto…</p>}>
      <Content />
    </Suspense>
  );
}
