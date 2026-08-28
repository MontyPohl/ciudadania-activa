import type { LocationValue } from "@/features/location/location";
import type {
  CreateReportResponse,
  PossibleDuplicateReport,
} from "@/features/reports/create/types";
import {
  categoryValidationError,
  descriptionValidationError,
  locationValidationError,
  photoValidationError,
  validateCreateReportDraft,
} from "@/features/reports/create/validation";
import type { CreateReportErrorCode } from "@/lib/api/reports";

export const CREATE_REPORT_STEPS = [
  "category",
  "description",
  "photo",
  "location",
  "confirmation",
] as const;

export type CreateReportStep = (typeof CREATE_REPORT_STEPS)[number];

export type DuplicateQueryState =
  | { status: "idle"; candidates: [] }
  | { status: "loading"; candidates: []; requestKey: string }
  | { status: "ready"; candidates: PossibleDuplicateReport[]; requestKey: string }
  | { status: "error"; candidates: []; requestKey: string };

export type ReportSubmissionState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "success"; result: CreateReportResponse }
  | { status: "error"; code: Exclude<CreateReportErrorCode, "UNCERTAIN_RESULT"> }
  | { status: "uncertain" };

export interface CreateReportWizardState {
  step: CreateReportStep;
  categoryId: string;
  description: string;
  photo: File | null;
  location: LocationValue | null;
  duplicates: DuplicateQueryState;
  submission: ReportSubmissionState;
}

export type CreateReportWizardAction =
  | { type: "SET_CATEGORY"; value: string }
  | { type: "SET_DESCRIPTION"; value: string }
  | { type: "SET_PHOTO"; value: File | null }
  | { type: "SET_LOCATION"; value: LocationValue | null }
  | { type: "GO_TO_STEP"; step: CreateReportStep }
  | { type: "NEXT" }
  | { type: "BACK" }
  | { type: "DUPLICATES_LOADING"; requestKey: string }
  | { type: "DUPLICATES_READY"; requestKey: string; candidates: PossibleDuplicateReport[] }
  | { type: "DUPLICATES_FAILED"; requestKey: string }
  | { type: "SUBMITTING" }
  | { type: "SUBMIT_SUCCEEDED"; result: CreateReportResponse }
  | { type: "SUBMIT_FAILED"; code: CreateReportErrorCode }
  | { type: "RESET" };

export function initialCreateReportWizardState(): CreateReportWizardState {
  return {
    step: "category",
    categoryId: "",
    description: "",
    photo: null,
    location: null,
    duplicates: { status: "idle", candidates: [] },
    submission: { status: "idle" },
  };
}

function stepIsValid(state: CreateReportWizardState): boolean {
  if (state.step === "category") return categoryValidationError(state.categoryId) === null;
  if (state.step === "description") return descriptionValidationError(state.description) === null;
  if (state.step === "photo") return photoValidationError(state.photo) === null;
  if (state.step === "location") return locationValidationError(state.location) === null;
  return true;
}

function resetSubmission(): ReportSubmissionState {
  return { status: "idle" };
}

function duplicateInputRequestKey(state: CreateReportWizardState): string | null {
  if (
    state.step !== "confirmation" ||
    categoryValidationError(state.categoryId) !== null ||
    locationValidationError(state.location) !== null ||
    state.location === null
  ) {
    return null;
  }
  return [state.categoryId, state.location.latitude, state.location.longitude].join(":");
}

export function canQueryPossibleDuplicates(state: CreateReportWizardState): boolean {
  return duplicateInputRequestKey(state) !== null && state.duplicates.status !== "loading";
}

export function possibleDuplicatesRequestKey(state: CreateReportWizardState): string | null {
  return canQueryPossibleDuplicates(state) ? duplicateInputRequestKey(state) : null;
}

function canStartSubmission(state: CreateReportWizardState): boolean {
  if (state.step !== "confirmation") return false;
  if (["submitting", "success", "uncertain"].includes(state.submission.status)) return false;
  return (
    Object.keys(
      validateCreateReportDraft({
        categoryId: state.categoryId,
        description: state.description,
        photo: state.photo,
        location: state.location,
      }),
    ).length === 0
  );
}

export function createReportWizardReducer(
  state: CreateReportWizardState,
  action: CreateReportWizardAction,
): CreateReportWizardState {
  if (action.type === "RESET") {
    return state.submission.status === "submitting" || state.submission.status === "uncertain"
      ? state
      : initialCreateReportWizardState();
  }
  if (
    state.submission.status === "uncertain" ||
    state.submission.status === "success" ||
    (state.submission.status === "submitting" &&
      action.type !== "SUBMIT_SUCCEEDED" &&
      action.type !== "SUBMIT_FAILED")
  ) {
    return state;
  }
  if (action.type === "SET_CATEGORY") {
    return {
      ...state,
      categoryId: action.value,
      duplicates: { status: "idle", candidates: [] },
      submission: resetSubmission(),
    };
  }
  if (action.type === "SET_DESCRIPTION") {
    return { ...state, description: action.value, submission: resetSubmission() };
  }
  if (action.type === "SET_PHOTO") {
    return { ...state, photo: action.value, submission: resetSubmission() };
  }
  if (action.type === "SET_LOCATION") {
    return {
      ...state,
      location: action.value,
      duplicates: { status: "idle", candidates: [] },
      submission: resetSubmission(),
    };
  }
  if (action.type === "GO_TO_STEP") {
    const currentIndex = CREATE_REPORT_STEPS.indexOf(state.step);
    const targetIndex = CREATE_REPORT_STEPS.indexOf(action.step);
    if (targetIndex >= currentIndex) return state;
    return {
      ...state,
      step: action.step,
      duplicates:
        action.step === "confirmation" ? state.duplicates : { status: "idle", candidates: [] },
    };
  }
  if (action.type === "NEXT") {
    if (!stepIsValid(state)) return state;
    const index = CREATE_REPORT_STEPS.indexOf(state.step);
    return index === CREATE_REPORT_STEPS.length - 1
      ? state
      : { ...state, step: CREATE_REPORT_STEPS[index + 1] };
  }
  if (action.type === "BACK") {
    const index = CREATE_REPORT_STEPS.indexOf(state.step);
    return index === 0
      ? state
      : {
          ...state,
          step: CREATE_REPORT_STEPS[index - 1],
          duplicates:
            state.step === "confirmation" ? { status: "idle", candidates: [] } : state.duplicates,
        };
  }
  if (action.type === "DUPLICATES_LOADING") {
    return possibleDuplicatesRequestKey(state) === action.requestKey
      ? {
          ...state,
          duplicates: { status: "loading", candidates: [], requestKey: action.requestKey },
        }
      : state;
  }
  if (action.type === "DUPLICATES_READY") {
    return state.duplicates.status === "loading" &&
      state.duplicates.requestKey === action.requestKey &&
      duplicateInputRequestKey(state) === action.requestKey
      ? {
          ...state,
          duplicates: {
            status: "ready",
            candidates: action.candidates,
            requestKey: action.requestKey,
          },
        }
      : state;
  }
  if (action.type === "DUPLICATES_FAILED") {
    return state.duplicates.status === "loading" &&
      state.duplicates.requestKey === action.requestKey &&
      duplicateInputRequestKey(state) === action.requestKey
      ? {
          ...state,
          duplicates: { status: "error", candidates: [], requestKey: action.requestKey },
        }
      : state;
  }
  if (action.type === "SUBMITTING") {
    return canStartSubmission(state) ? { ...state, submission: { status: "submitting" } } : state;
  }
  if (action.type === "SUBMIT_SUCCEEDED") {
    return state.submission.status === "submitting"
      ? { ...state, submission: { status: "success", result: action.result } }
      : state;
  }
  if (state.submission.status !== "submitting") return state;
  if (action.code === "UNCERTAIN_RESULT") {
    return { ...state, submission: { status: "uncertain" } };
  }
  return { ...state, submission: { status: "error", code: action.code } };
}

/** Una segunda invocación concurrente comparte la promesa activa y no repite la operación. */
export function singleFlight<TResult>(operation: () => Promise<TResult>): () => Promise<TResult> {
  let active: Promise<TResult> | null = null;
  return () => {
    if (active !== null) return active;
    active = operation().finally(() => {
      active = null;
    });
    return active;
  };
}
