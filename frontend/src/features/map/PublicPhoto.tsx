/* eslint-disable @next/next/no-img-element -- Public proxy media uses ordinary lazy images. */
import { STATUS_LABELS } from "./presentation";
import type { PublicReport } from "@/types/api";

export function publicMediaUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_URL;
  return base ? new URL(path, base).href : path;
}

export function PublicPhoto({
  report,
  enlarge = false,
  thumbnail = false,
}: {
  report: PublicReport;
  enlarge?: boolean;
  thumbnail?: boolean;
}) {
  if (!report.photo_url) return null;
  const src = publicMediaUrl(report.photo_url);
  const photo = (
    <img
      src={src}
      loading="lazy"
      referrerPolicy="no-referrer"
      alt={`Reporte de ${report.category.name} · ${STATUS_LABELS[report.status]}`}
      className={
        thumbnail
          ? "mb-3 h-24 w-full rounded-lg object-cover"
          : "my-4 max-h-96 w-full rounded-xl object-contain bg-canvas"
      }
    />
  );
  return enlarge ? (
    <a
      href={src}
      target="_blank"
      rel="noreferrer"
      className="block rounded-xl focus-visible:outline-2 focus-visible:outline-focus"
    >
      {photo}
      <span className="text-sm font-semibold text-primary underline">
        Ampliar foto (nueva pestaña)
      </span>
    </a>
  ) : (
    photo
  );
}
