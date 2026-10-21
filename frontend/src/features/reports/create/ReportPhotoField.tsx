"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";

import { photoValidationError } from "@/features/reports/create/validation";

interface ReportPhotoFieldProps {
  value: File | null;
  onChange: (value: File | null) => void;
  disabled?: boolean;
  error?: string;
}

export function ReportPhotoField({
  value,
  onChange,
  disabled = false,
  error,
}: ReportPhotoFieldProps) {
  const generatedId = useId();
  const inputId = generatedId + "-photo";
  const helpId = generatedId + "-help";
  const errorId = generatedId + "-error";
  const inputRef = useRef<HTMLInputElement>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const visibleError = localError ?? error;

  function selectPhoto(event: React.ChangeEvent<HTMLInputElement>) {
    if (disabled) return;
    const nextFile = event.target.files?.[0] ?? null;
    const problem = photoValidationError(nextFile);
    if (problem !== null) {
      setLocalError(problem);
      event.target.value = "";
      return;
    }
    setLocalError(null);
    onChange(nextFile);
  }

  function removePhoto() {
    if (disabled) return;
    setLocalError(null);
    onChange(null);
    if (inputRef.current !== null) inputRef.current.value = "";
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className="text-sm font-semibold">
          Fotografía de la incidencia
        </label>
        <input
          ref={inputRef}
          id={inputId}
          name="photo"
          type="file"
          accept="image/jpeg,image/png"
          disabled={disabled}
          aria-invalid={visibleError !== undefined && visibleError !== null}
          aria-describedby={`${helpId}${visibleError ? ` ${errorId}` : ""}`}
          onChange={selectPhoto}
          className="max-w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-brand-purple file:px-4 file:py-2 file:font-semibold file:text-white disabled:opacity-55"
        />
        <p id={helpId} className="text-xs text-brand-black/65">
          JPEG o PNG, hasta 5 MiB. La imagen no se comprime ni se transforma.
        </p>
        {visibleError && (
          <p id={errorId} role="alert" className="text-sm font-medium text-brand-orange-ink">
            {visibleError}
          </p>
        )}
      </div>

      {value !== null && (
        <figure className="min-w-0 rounded-xl border border-brand-black/10 bg-brand-purple/5 p-3">
          <ReportPhotoPreview file={value} />
          <figcaption className="mt-2 break-words text-xs text-brand-black/70">
            <span className="font-semibold">{value.name}</span> · {formatFileSize(value.size)}
          </figcaption>
          <div className="mt-3 flex flex-wrap gap-2">
            <label
              htmlFor={inputId}
              className={`rounded-md border border-brand-purple px-3 py-2 text-sm font-semibold text-brand-purple ${disabled ? "pointer-events-none opacity-55" : "cursor-pointer"}`}
            >
              Reemplazar fotografía
            </label>
            <button
              type="button"
              onClick={removePhoto}
              disabled={disabled}
              className="rounded-md border border-brand-orange px-3 py-2 text-sm font-semibold text-brand-orange-ink disabled:opacity-55"
            >
              Quitar fotografía
            </button>
          </div>
        </figure>
      )}
    </div>
  );
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

export function ReportPhotoPreview({ file }: { file: File }) {
  return <ObjectUrlPhotoPreview key={fileKey(file)} file={file} />;
}

const FILE_KEYS = new WeakMap<File, number>();
let nextFileKey = 1;

function fileKey(file: File): number {
  const existing = FILE_KEYS.get(file);
  if (existing !== undefined) return existing;
  const key = nextFileKey;
  nextFileKey += 1;
  FILE_KEYS.set(file, key);
  return key;
}

function ObjectUrlPhotoPreview({ file }: { file: File }) {
  const [previewUrl] = useState(() => URL.createObjectURL(file));

  useEffect(() => {
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  return (
    <Image
      src={previewUrl}
      unoptimized
      width={960}
      height={720}
      alt="Vista previa de la fotografía seleccionada"
      className="max-h-80 w-full rounded-lg object-contain"
    />
  );
}
