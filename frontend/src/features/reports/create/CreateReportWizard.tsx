"use client";

import Link from "next/link";
import { forwardRef, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { LocationPicker } from "@/features/location/LocationPicker";
import {
  CategoryStep,
  ConfirmationStep,
  DescriptionStep,
  STEP_LABELS,
  WizardProgress,
  type CategoryCatalog,
} from "@/features/reports/create/CreateReportSteps";
import { ReportPhotoField } from "@/features/reports/create/ReportPhotoField";
import { ReportSuccess } from "@/features/reports/create/ReportSuccess";
import {
  CREATE_REPORT_STEPS,
  createReportWizardReducer,
  initialCreateReportWizardState,
  singleFlight,
  type CreateReportStep,
  type ReportSubmissionState,
} from "@/features/reports/create/state";
import type {
  CreateReportField,
  CreateReportValidationErrors,
} from "@/features/reports/create/validation";
import {
  categoryValidationError,
  descriptionValidationError,
  locationValidationError,
  photoValidationError,
  validateCreateReportDraft,
} from "@/features/reports/create/validation";
import { getPublicCategories } from "@/lib/api/publicReports";
import {
  CreateReportError,
  createReport,
  getPossibleReportDuplicates,
  type CreateReportErrorCode,
} from "@/lib/api/reports";
import type { PublicCategory } from "@/types/api";

const FIELD_STEP: Record<CreateReportField, CreateReportStep> = {
  category: "category",
  description: "description",
  photo: "photo",
  location: "location",
};

const FIELD_ID: Record<CreateReportField, string> = {
  category: "report-category",
  description: "report-description",
  photo: "report-photo-step",
  location: "report-location-step",
};

export function CreateReportWizard() {
  const [state, dispatch] = useReducer(
    createReportWizardReducer,
    undefined,
    initialCreateReportWizardState,
  );
  const [catalog, setCatalog] = useState<CategoryCatalog>({ status: "loading", items: [] });
  const [errors, setErrors] = useState<CreateReportValidationErrors>({});
  const categoryControllerRef = useRef<AbortController | null>(null);
  const duplicateRequestRef = useRef(0);
  const stateRef = useRef(state);
  const catalogRef = useRef(catalog);
  const submitButtonRef = useRef<HTMLButtonElement>(null);
  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const submissionMessageRef = useRef<HTMLDivElement>(null);
  const pendingFocusRef = useRef<CreateReportField | null>(null);
  const submitOperationRef = useRef<() => Promise<void>>(async () => undefined);
  const singleSubmitRef = useRef<(() => Promise<void>) | null>(null);

  useEffect(() => {
    stateRef.current = state;
    catalogRef.current = catalog;
  }, [catalog, state]);

  const requestCategories = useCallback(async (): Promise<PublicCategory[] | null> => {
    categoryControllerRef.current?.abort();
    const controller = new AbortController();
    categoryControllerRef.current = controller;
    try {
      const items = await getPublicCategories(controller.signal);
      if (controller.signal.aborted) return null;
      setCatalog({ status: "ready", items });
      return items;
    } catch {
      if (controller.signal.aborted) return null;
      setCatalog({ status: "error", items: [] });
      return null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    categoryControllerRef.current = controller;
    getPublicCategories(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setCatalog({ status: "ready", items });
      })
      .catch(() => {
        if (!controller.signal.aborted) setCatalog({ status: "error", items: [] });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (state.step !== "confirmation") return;
    if (
      categoryValidationError(state.categoryId) !== null ||
      locationValidationError(state.location) !== null ||
      state.location === null
    ) {
      return;
    }
    const requestKey = [state.categoryId, state.location.latitude, state.location.longitude].join(
      ":",
    );

    duplicateRequestRef.current += 1;
    const requestId = duplicateRequestRef.current;
    const controller = new AbortController();
    dispatch({ type: "DUPLICATES_LOADING", requestKey });
    getPossibleReportDuplicates(
      { categoryId: state.categoryId, location: state.location },
      controller.signal,
    )
      .then((candidates) => {
        if (controller.signal.aborted || duplicateRequestRef.current !== requestId) return;
        dispatch({ type: "DUPLICATES_READY", requestKey, candidates });
      })
      .catch(() => {
        if (controller.signal.aborted || duplicateRequestRef.current !== requestId) return;
        dispatch({ type: "DUPLICATES_FAILED", requestKey });
      });

    return () => {
      controller.abort();
      duplicateRequestRef.current += 1;
    };
  }, [state.categoryId, state.location, state.step]);

  useEffect(() => {
    stepHeadingRef.current?.focus();
    const field = pendingFocusRef.current;
    if (field !== null && FIELD_STEP[field] === state.step) {
      pendingFocusRef.current = null;
      document.getElementById(FIELD_ID[field])?.focus();
    }
  }, [state.step]);

  useEffect(() => {
    if (state.submission.status === "error" || state.submission.status === "uncertain") {
      submissionMessageRef.current?.focus();
    }
  }, [state.submission.status]);

  const selectedCategory = useMemo(
    () => catalog.items.find((item) => item.id === state.categoryId) ?? null,
    [catalog.items, state.categoryId],
  );
  const locked =
    state.submission.status === "submitting" || state.submission.status === "uncertain";
  const stepIndex = CREATE_REPORT_STEPS.indexOf(state.step);
  const visibleValidationFields = (Object.keys(errors) as CreateReportField[]).filter(
    (field) => errors[field] !== undefined,
  );

  function focusField(field: CreateReportField) {
    const targetStep = FIELD_STEP[field];
    if (targetStep === state.step) {
      document.getElementById(FIELD_ID[field])?.focus();
      return;
    }
    pendingFocusRef.current = field;
    dispatch({ type: "GO_TO_STEP", step: targetStep });
  }

  function validateCurrentStep(): boolean {
    let field: CreateReportField;
    let problem: string | null;
    if (state.step === "category") {
      field = "category";
      problem = categoryValidationError(state.categoryId);
      if (problem === null && selectedCategory === null)
        problem = "Seleccioná una categoría disponible.";
    } else if (state.step === "description") {
      field = "description";
      problem = descriptionValidationError(state.description);
    } else if (state.step === "photo") {
      field = "photo";
      problem = photoValidationError(state.photo);
    } else if (state.step === "location") {
      field = "location";
      problem = locationValidationError(state.location);
    } else {
      return true;
    }

    if (problem !== null) {
      setErrors((current) => ({ ...current, [field]: problem }));
      focusField(field);
      return false;
    }
    setErrors((current) => {
      const next = { ...current };
      delete next[field];
      return next;
    });
    return true;
  }

  function goNext() {
    if (!validateCurrentStep()) return;
    dispatch({ type: "NEXT" });
  }

  async function performSubmit() {
    const snapshot = stateRef.current;
    if (snapshot.submission.status === "submitting" || snapshot.submission.status === "uncertain") {
      return;
    }
    const validationErrors = validateCreateReportDraft(snapshot);
    const categoryExists = catalogRef.current.items.some((item) => item.id === snapshot.categoryId);
    if (!categoryExists) validationErrors.category = "Seleccioná una categoría disponible.";
    const firstError = Object.keys(validationErrors)[0] as CreateReportField | undefined;
    if (firstError !== undefined) {
      setErrors(validationErrors);
      focusField(firstError);
      return;
    }

    dispatch({ type: "SUBMITTING" });
    try {
      const result = await createReport({
        categoryId: snapshot.categoryId,
        description: snapshot.description,
        photo: snapshot.photo as File,
        location: snapshot.location!,
      });
      dispatch({ type: "SUBMIT_SUCCEEDED", result });
    } catch (error) {
      const code: CreateReportErrorCode =
        error instanceof CreateReportError ? error.code : "UNKNOWN_ERROR";
      dispatch({ type: "SUBMIT_FAILED", code });
      if (code === "VALIDATION_ERROR") {
        setCatalog((current) => ({ status: "loading", items: current.items }));
        const refreshed = await requestCategories();
        if (refreshed !== null && !refreshed.some((item) => item.id === snapshot.categoryId)) {
          dispatch({ type: "SET_CATEGORY", value: "" });
          setErrors((current) => ({ ...current, category: "Elegí nuevamente la categoría." }));
          pendingFocusRef.current = "category";
          dispatch({ type: "GO_TO_STEP", step: "category" });
        }
      }
    }
  }

  useEffect(() => {
    submitOperationRef.current = performSubmit;
  });

  useEffect(() => {
    singleSubmitRef.current = singleFlight(() => submitOperationRef.current());
    return () => {
      singleSubmitRef.current = null;
    };
  }, []);

  function handleFormSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.step === "confirmation") {
      void singleSubmitRef.current?.();
      return;
    }
    goNext();
  }

  function retryCategories() {
    setCatalog({ status: "loading", items: [] });
    void requestCategories();
  }

  if (state.submission.status === "success") {
    return (
      <ReportSuccess
        result={state.submission.result}
        onCreateAnother={() => {
          setErrors({});
          setCatalog((current) => ({ status: "loading", items: current.items }));
          dispatch({ type: "RESET" });
          void requestCategories();
        }}
      />
    );
  }

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10"
    >
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold text-brand-purple sm:text-3xl">Crear un reporte</h1>
        <p className="max-w-2xl text-sm text-brand-black/70">
          Contanos qué ocurre y dónde. La institución correspondiente revisará la información.
        </p>
      </header>

      <WizardProgress currentStep={state.step} />
      <p className="sr-only" role="status" aria-live="polite">
        Paso {stepIndex + 1} de 5: {STEP_LABELS[state.step]}
      </p>

      <form onSubmit={handleFormSubmit} noValidate className="flex min-w-0 flex-col gap-6">
        {visibleValidationFields.length > 0 && (
          <div
            role="alert"
            className="rounded-xl border border-brand-orange/40 bg-brand-orange/10 p-4"
          >
            <p className="text-sm font-semibold text-brand-orange-ink">
              Revisá los campos señalados:
            </p>
            <ul className="mt-2 list-disc pl-5 text-sm text-brand-black/75">
              {visibleValidationFields.map((field) => (
                <li key={field}>{STEP_LABELS[FIELD_STEP[field]]}</li>
              ))}
            </ul>
          </div>
        )}
        <section className="min-w-0 rounded-2xl border border-brand-black/10 bg-white p-4 shadow-sm sm:p-6">
          <h2 ref={stepHeadingRef} tabIndex={-1} className="text-xl font-bold text-brand-purple">
            {STEP_LABELS[state.step]}
          </h2>
          <div className="mt-5">
            {state.step === "category" && (
              <CategoryStep
                catalog={catalog}
                value={state.categoryId}
                error={errors.category}
                disabled={locked}
                onChange={(value) => {
                  setErrors((current) => ({ ...current, category: undefined }));
                  dispatch({ type: "SET_CATEGORY", value });
                }}
                onRetry={retryCategories}
              />
            )}
            {state.step === "description" && (
              <DescriptionStep
                value={state.description}
                error={errors.description}
                disabled={locked}
                onChange={(value) => {
                  setErrors((current) => ({ ...current, description: undefined }));
                  dispatch({ type: "SET_DESCRIPTION", value });
                }}
              />
            )}
            {state.step === "photo" && (
              <div id="report-photo-step" tabIndex={-1}>
                <ReportPhotoField
                  value={state.photo}
                  error={errors.photo}
                  disabled={locked}
                  onChange={(value) => {
                    setErrors((current) => ({ ...current, photo: undefined }));
                    dispatch({ type: "SET_PHOTO", value });
                  }}
                />
              </div>
            )}
            {state.step === "location" && (
              <div id="report-location-step" tabIndex={-1}>
                <LocationPicker
                  value={state.location}
                  onChange={(value) => {
                    setErrors((current) => ({ ...current, location: undefined }));
                    dispatch({ type: "SET_LOCATION", value });
                  }}
                  disabled={locked}
                  aria-describedby={errors.location ? "report-location-error" : undefined}
                />
                {errors.location && (
                  <p
                    id="report-location-error"
                    role="alert"
                    className="mt-2 text-sm font-medium text-brand-orange-ink"
                  >
                    {errors.location}
                  </p>
                )}
              </div>
            )}
            {state.step === "confirmation" && (
              <ConfirmationStep
                state={state}
                categoryName={selectedCategory?.name ?? "Categoría no disponible"}
                onContinue={() => submitButtonRef.current?.focus()}
              />
            )}
          </div>
        </section>

        {(state.submission.status === "error" || state.submission.status === "uncertain") && (
          <SubmissionMessage
            ref={submissionMessageRef}
            status={state.submission}
            onChangePhoto={() => {
              pendingFocusRef.current = "photo";
              dispatch({ type: "GO_TO_STEP", step: "photo" });
            }}
          />
        )}

        <p className="sr-only" role="status" aria-live="polite">
          {state.submission.status === "submitting" ? "Creando el reporte…" : ""}
        </p>

        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
          <button
            type="button"
            onClick={() => dispatch({ type: "BACK" })}
            disabled={stepIndex === 0 || locked}
            className="rounded-md border border-brand-purple px-5 py-2.5 text-sm font-semibold text-brand-purple disabled:cursor-not-allowed disabled:opacity-45"
          >
            Volver
          </button>
          <button
            ref={submitButtonRef}
            type="submit"
            disabled={locked || (state.step === "category" && catalog.status !== "ready")}
            className="rounded-md bg-brand-purple px-5 py-2.5 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state.step !== "confirmation"
              ? "Continuar"
              : state.submission.status === "submitting"
                ? "Creando reporte…"
                : state.submission.status === "error"
                  ? "Intentar de nuevo"
                  : "Crear reporte"}
          </button>
        </div>
      </form>
    </main>
  );
}

const SUBMISSION_MESSAGES: Record<Exclude<CreateReportErrorCode, "UNCERTAIN_RESULT">, string> = {
  VALIDATION_ERROR:
    "El servidor rechazó algún dato. Revisá el formulario antes de volver a intentar.",
  SESSION_EXPIRED:
    "Tu sesión venció y el envío no se repitió. Tus datos siguen guardados en este formulario.",
  NOT_VERIFIED_OR_FORBIDDEN:
    "No tenés autorización para crear este reporte. Verificá el estado de tu identidad y tu rol.",
  DUPLICATE_PHOTO:
    "Esta fotografía ya fue utilizada en otro reporte. Reemplazala antes de intentar nuevamente.",
  RATE_LIMIT:
    "Alcanzaste temporalmente el límite de creación. Tus datos se conservaron; intentá más tarde.",
  SERVICE_UNAVAILABLE: "El servicio no está disponible en este momento. Tus datos se conservaron.",
  UNKNOWN_ERROR: "No se pudo completar la creación. Tus datos se conservaron.",
};

const SubmissionMessage = forwardRef<
  HTMLDivElement,
  {
    status: Extract<ReportSubmissionState, { status: "error" | "uncertain" }>;
    onChangePhoto: () => void;
  }
>(function SubmissionMessage({ status, onChangePhoto }, ref) {
  const uncertain = status.status === "uncertain";
  const code = status.status === "error" ? status.code : null;
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      aria-live="assertive"
      className={`rounded-xl border p-4 ${uncertain ? "border-brand-orange bg-brand-orange/10" : "border-brand-black/15 bg-white"}`}
    >
      <h2 className="font-bold text-brand-orange-ink">
        {uncertain
          ? "No pudimos confirmar si el reporte fue creado"
          : "No se pudo crear el reporte"}
      </h2>
      <p className="mt-1 text-sm text-brand-black/75">
        {uncertain
          ? "La solicitud pudo haber llegado al servidor. No la reenvíes inmediatamente; conservamos todos los datos y bloqueamos nuevos cambios."
          : SUBMISSION_MESSAGES[code as Exclude<CreateReportErrorCode, "UNCERTAIN_RESULT">]}
      </p>
      {code === "SESSION_EXPIRED" && (
        <Link
          href="/login"
          className="mt-3 inline-block text-sm font-semibold text-brand-purple underline"
        >
          Ir a iniciar sesión
        </Link>
      )}
      {code === "NOT_VERIFIED_OR_FORBIDDEN" && (
        <Link
          href="/verificacion"
          className="mt-3 inline-block text-sm font-semibold text-brand-purple underline"
        >
          Revisar verificación
        </Link>
      )}
      {code === "DUPLICATE_PHOTO" && (
        <button
          type="button"
          onClick={onChangePhoto}
          className="mt-3 rounded-md border border-brand-purple px-4 py-2 text-sm font-semibold text-brand-purple"
        >
          Reemplazar fotografía
        </button>
      )}
    </div>
  );
});
