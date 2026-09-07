"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { OperatorReportDetail } from "@/features/reports/operator/OperatorReportDetail";

function DetailContent() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const id = typeof params.id === "string" ? params.id : "";
  return (
    <>
      <Link href="/bandeja" className="text-sm font-medium text-brand-purple underline">
        Volver a la bandeja
      </Link>
      <OperatorReportDetail
        id={id}
        initialInstitutionId={search.get("institution_id") ?? undefined}
      />
    </>
  );
}

export default function OperatorReportPage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-10">
      <h1 className="text-2xl font-bold text-brand-purple">Detalle del reporte</h1>
      <Suspense fallback={<p role="status">Cargando contexto…</p>}>
        <DetailContent />
      </Suspense>
    </main>
  );
}
