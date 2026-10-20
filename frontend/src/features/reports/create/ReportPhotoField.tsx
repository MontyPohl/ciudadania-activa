"use client";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
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
  const publicNoticeId = generatedId + "-public-notice";
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
        <Input
          ref={inputRef}
          id={inputId}
          name="photo"
          type="file"
          accept="image/jpeg,image/png"
          disabled={disabled}
          aria-invalid={visibleError !== undefined && visibleError !== null}
          aria-describedby={`${helpId} ${publicNoticeId}${visibleError ? ` ${errorId}` : ""}`}
          onChange={selectPhoto}
          className="max-w-full file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-4 file:min-h-11 file:py-2 file:font-semibold file:text-white"
        />
        <p id={helpId} className="text-xs text-muted">
          JPEG o PNG, hasta 5 MiB. La imagen no se comprime ni se transforma.
        </p>
        <p id={publicNoticeId} className="rounded-lg bg-primary/5 p-3 text-sm text-primary">
          La foto será pública: evitá caras, patentes y datos personales.
        </p>
        {visibleError && (
          <p id={errorId} role="alert" className="text-sm font-medium text-danger-ink">
            {visibleError}
          </p>
        )}
      </div>

      {value !== null && (
        <figure className="min-w-0 rounded-xl border border-border bg-primary/5 p-3">
          <ReportPhotoPreview file={value} />
          <figcaption className="mt-2 break-words text-xs text-muted">
            <span className="font-semibold">{value.name}</span> · {formatFileSize(value.size)}
          </figcaption>
          <div className="mt-3 flex flex-wrap gap-2">
            <label
              htmlFor={inputId}
              className={`rounded-md border border-primary px-3 py-2 text-sm font-semibold text-primary ${disabled ? "pointer-events-none opacity-55" : "cursor-pointer"}`}
            >
              Reemplazar fotografía
            </label>
            <Button
              variant="secondary"
              type="button"
              onClick={removePhoto}
              disabled={disabled}
              className="text-sm"
            >
              Quitar fotografía
            </Button>
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
