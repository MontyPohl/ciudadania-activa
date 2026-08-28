import {
  canQueryPossibleDuplicates,
  createReportWizardReducer,
  initialCreateReportWizardState,
  possibleDuplicatesRequestKey,
  singleFlight,
  type CreateReportWizardState,
} from "@/features/reports/create/state";
import { reportCreationAccess } from "@/features/reports/create/types";

const CATEGORY_ID = "11111111-1111-5111-8111-111111111111";
const LOCATION = { latitude: -25.123456789123, longitude: -57.987654321987 };

function photo(): File {
  return new File(["foto"], "foto.jpg", { type: "image/jpeg" });
}

describe("estado puro del asistente", () => {
  it("declara los cuatro estados de acceso sin duplicar la autenticación", () => {
    expect(reportCreationAccess(null)).toBe("anonymous");
    expect(reportCreationAccess({ role: "operator", verification_status: "APROBADO" })).toBe(
      "wrong-role",
    );
    expect(reportCreationAccess({ role: "citizen", verification_status: "PENDIENTE" })).toBe(
      "citizen-unverified",
    );
    expect(reportCreationAccess({ role: "citizen", verification_status: "APROBADO" })).toBe(
      "citizen-verified",
    );
  });

  it("impide avanzar con el paso inválido y conserva los datos originales", () => {
    let state = initialCreateReportWizardState();
    expect(createReportWizardReducer(state, { type: "NEXT" })).toBe(state);
    expect(createReportWizardReducer(state, { type: "GO_TO_STEP", step: "confirmation" })).toBe(
      state,
    );

    state = createReportWizardReducer(state, { type: "SET_CATEGORY", value: CATEGORY_ID });
    state = createReportWizardReducer(state, { type: "NEXT" });
    state = createReportWizardReducer(state, {
      type: "SET_DESCRIPTION",
      value: "  descripción original\n",
    });
    state = createReportWizardReducer(state, { type: "NEXT" });
    state = createReportWizardReducer(state, { type: "SET_PHOTO", value: photo() });
    state = createReportWizardReducer(state, { type: "NEXT" });
    state = createReportWizardReducer(state, { type: "SET_LOCATION", value: LOCATION });
    state = createReportWizardReducer(state, { type: "NEXT" });

    expect(state.step).toBe("confirmation");
    expect(state.description).toBe("  descripción original\n");
    expect(state.location).toBe(LOCATION);
    expect(canQueryPossibleDuplicates(state)).toBe(true);

    state = createReportWizardReducer(state, { type: "GO_TO_STEP", step: "photo" });
    expect(state.step).toBe("photo");
    expect(state.photo).not.toBeNull();
  });

  it("solo inicia duplicados en confirmación e invalida candidatos al cambiar la entrada", () => {
    let state = initialCreateReportWizardState();
    expect(createReportWizardReducer(state, { type: "DUPLICATES_LOADING", requestKey: "x" })).toBe(
      state,
    );
    state = {
      ...state,
      step: "confirmation",
      categoryId: CATEGORY_ID,
      location: LOCATION,
      duplicates: { status: "ready", candidates: [], requestKey: "anterior" },
    };
    state = createReportWizardReducer(state, { type: "SET_LOCATION", value: { ...LOCATION } });
    expect(state.duplicates).toEqual({ status: "idle", candidates: [] });
    const requestKey = possibleDuplicatesRequestKey(state);
    expect(requestKey).not.toBeNull();
    state = createReportWizardReducer(state, {
      type: "DUPLICATES_LOADING",
      requestKey: requestKey as string,
    });
    expect(state.duplicates.status).toBe("loading");
  });

  it("ignora una respuesta tardía de duplicados y conserva el fallo como no bloqueante", () => {
    let state: CreateReportWizardState = {
      ...initialCreateReportWizardState(),
      step: "confirmation",
      categoryId: CATEGORY_ID,
      location: LOCATION,
    };
    const requestKey = possibleDuplicatesRequestKey(state) as string;
    state = createReportWizardReducer(state, { type: "DUPLICATES_LOADING", requestKey });
    const loading = state;
    expect(
      createReportWizardReducer(state, {
        type: "DUPLICATES_READY",
        requestKey: "petición-vieja",
        candidates: [],
      }),
    ).toBe(loading);
    state = createReportWizardReducer(state, { type: "DUPLICATES_FAILED", requestKey });
    expect(state.duplicates.status).toBe("error");
  });

  it("un resultado incierto conserva datos y bloquea un nuevo submit", () => {
    let state: CreateReportWizardState = {
      ...initialCreateReportWizardState(),
      step: "confirmation",
      categoryId: CATEGORY_ID,
      description: "Descripción",
      photo: photo(),
      location: LOCATION,
    };
    state = createReportWizardReducer(state, { type: "SUBMITTING" });
    expect(state.submission.status).toBe("submitting");
    state = createReportWizardReducer(state, {
      type: "SUBMIT_FAILED",
      code: "UNCERTAIN_RESULT",
    });
    expect(state.submission).toEqual({ status: "uncertain" });
    expect(state.categoryId).toBe(CATEGORY_ID);
    expect(state.location).toBe(LOCATION);
    expect(createReportWizardReducer(state, { type: "SUBMITTING" })).toBe(state);
    expect(
      createReportWizardReducer(state, { type: "SET_DESCRIPTION", value: "otro intento" }),
    ).toBe(state);
    expect(createReportWizardReducer(state, { type: "RESET" })).toBe(state);
  });

  it("comparte una sola promesa ante dos submits simultáneos", async () => {
    let resolve!: (value: string) => void;
    const operation = vi.fn(
      () =>
        new Promise<string>((done) => {
          resolve = done;
        }),
    );
    const submit = singleFlight(operation);

    const first = submit();
    const second = submit();
    expect(first).toBe(second);
    expect(operation).toHaveBeenCalledOnce();
    resolve("ok");
    await expect(first).resolves.toBe("ok");
  });
});
