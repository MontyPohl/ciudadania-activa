import type { Page } from "@playwright/test";

import type {
  LedgerMovement,
  MonthlyRanking,
  PaginatedResponse,
  Redemption,
  RewardsCatalog,
  RewardsSummary,
  SessionRefresh,
  SessionInstitution,
  SessionUser,
} from "../src/types/api";
import { ANDE_ID, openOperatorQueue, OPERATOR_REPORT_ID } from "./operator-report-fixtures";

const PERIOD = "2026-09";
const PAGE_SIZE = 20;
const ande: SessionInstitution = { id: ANDE_ID, name: "ANDE", slug: "ande" };
const essap: SessionInstitution = {
  id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  name: "ESSAP",
  slug: "essap",
};
export const JOURNEY_AUTHOR: SessionUser = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "journey-author@example.test",
  full_name: "Ciudadanía E2E",
  role: "citizen",
  verification_status: "APROBADO",
  is_verified_citizen: true,
  institutions: [],
};

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const id = (number: number) => `14000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const redemption: Redemption = {
  id: id(900),
  code: "CA-7K3M-9QXP-2D4F-HJ8R",
  period: PERIOD,
  credits_spent: 1000,
  simulated: true,
  created_at: "2026-09-05T12:00:00Z",
  reward: { id: id(901), name: "Vale simulado de ESSAP" },
  institution: essap,
};

function movement(number: number, fields: Partial<LedgerMovement>): LedgerMovement {
  return {
    id: id(number),
    event_type: "REPORT_CREATED",
    status: "ACREDITADO",
    impact_delta: 20,
    credit_delta: 20,
    institution: ande,
    report_id: OPERATOR_REPORT_ID,
    redemption_id: null,
    created_at: "2026-09-01T10:00:00Z",
    credited_at: "2026-09-01T11:00:00Z",
    voided_at: null,
    reversed_at: null,
    ...fields,
  };
}

function negativeBalanceMovements(): LedgerMovement[] {
  // D-01: había 1.000 créditos, se canjearon y se revirtieron 50. Quedan -50.
  const reversedAt = "2026-09-06T12:00:00Z";
  return [
    ...[30, 20].map((amount, index) =>
      movement(800 - index, {
        event_type: "REVERSAL",
        status: "REVERTIDO",
        impact_delta: -amount,
        credit_delta: -amount,
        institution: essap,
        report_id: id(700),
        created_at: reversedAt,
        credited_at: null,
        reversed_at: reversedAt,
      }),
    ),
    movement(600, {
      event_type: "REDEMPTION",
      impact_delta: 0,
      credit_delta: -1000,
      institution: essap,
      report_id: null,
      redemption_id: redemption.id,
      created_at: redemption.created_at,
      credited_at: redemption.created_at,
    }),
    ...[30, 20].map((amount, index) =>
      movement(500 - index, {
        event_type: index === 0 ? "VALIDATION_BONUS" : "REPORT_CREATED",
        status: "REVERTIDO",
        impact_delta: amount,
        credit_delta: amount,
        institution: essap,
        report_id: id(700),
        created_at: index === 0 ? "2026-09-04T12:00:00Z" : "2026-09-03T12:00:00Z",
        credited_at: "2026-09-04T12:00:00Z",
        reversed_at: reversedAt,
      }),
    ),
    // Diecinueve bonos previos de +50 conservan 950 créditos acreditados.
    ...Array.from({ length: 19 }, (_, index) =>
      movement(300 - index, {
        event_type: "RESOLUTION_BONUS",
        impact_delta: 50,
        credit_delta: 50,
        institution: essap,
        report_id: id(400 - index),
        created_at: "2026-09-02T12:00:00Z",
        credited_at: "2026-09-02T12:00:00Z",
      }),
    ),
  ];
}

export function createJourneyState(
  options: { validated?: boolean; negativeBalance?: boolean } = {},
) {
  const state = {
    validated: options.validated ?? false,
    transactionPages: [] as number[],
    movements(): LedgerMovement[] {
      return [
        ...(options.negativeBalance ? negativeBalanceMovements() : []),
        ...(state.validated
          ? [
              movement(2, {
                event_type: "VALIDATION_BONUS",
                impact_delta: 30,
                credit_delta: 30,
                created_at: "2026-09-01T11:00:00Z",
              }),
            ]
          : []),
        movement(1, {
          status: state.validated ? "ACREDITADO" : "PENDIENTE",
          institution: state.validated ? ande : null,
          credited_at: state.validated ? "2026-09-01T11:00:00Z" : null,
        }),
      ];
    },
    summary(): RewardsSummary {
      const rows = state.movements();
      const credited = rows.filter((row) => row.status === "ACREDITADO");
      return {
        impact: {
          historical: credited.reduce((sum, row) => sum + row.impact_delta, 0),
          current_month: credited.reduce((sum, row) => sum + row.impact_delta, 0),
          pending: rows
            .filter((row) => row.status === "PENDIENTE")
            .reduce((sum, row) => sum + row.impact_delta, 0),
        },
        credits_by_institution: [ande, essap]
          .filter((institution) => credited.some((row) => row.institution?.id === institution.id))
          .map((institution) => ({
            institution,
            balance: credited
              .filter((row) => row.institution?.id === institution.id)
              .reduce((sum, row) => sum + row.credit_delta, 0),
          })),
        period: PERIOD,
      };
    },
    transactions(page: number, baseURL: string): PaginatedResponse<LedgerMovement> {
      const rows = state.movements();
      const link = (number: number) =>
        new URL(`/api/v1/rewards/transactions/?page=${number}`, baseURL).href;
      return {
        count: rows.length,
        next: page * PAGE_SIZE < rows.length ? link(page + 1) : null,
        previous: page > 1 ? link(page - 1) : null,
        results: rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
      };
    },
    ranking(): MonthlyRanking {
      return {
        period: PERIOD,
        entries: state.validated
          ? [
              {
                position: 1,
                display_name: JOURNEY_AUTHOR.full_name,
                points: state.summary().impact.current_month,
                is_private: false,
                is_me: true,
              },
            ]
          : [],
      };
    },
  };
  return state;
}

export type JourneyState = ReturnType<typeof createJourneyState>;

async function installNetworkGuards(page: Page, baseURL: string): Promise<void> {
  const frontendOrigin = new URL(baseURL).origin;
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== frontendOrigin) {
      await route.abort();
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      await route.abort();
      throw new Error(`API sin simular: ${route.request().method()} ${url.pathname}`);
    }
    await route.fallback();
  });
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: png }),
  );
}

export async function mockJourneyCitizenApi(
  page: Page,
  state: JourneyState,
  baseURL: string,
): Promise<void> {
  await installNetworkGuards(page, baseURL);
  await page.route("**/api/auth/refresh", (route) => {
    const response: SessionRefresh = { access: "synthetic-journey-author-access" };
    return route.fulfill({ status: 200, json: response });
  });
  await page.route("**/api/v1/auth/me/", (route) =>
    route.fulfill({ status: 200, json: JOURNEY_AUTHOR }),
  );
  await page.route("**/api/v1/ranking/monthly/", (route) =>
    route.fulfill({ status: 200, json: state.ranking() }),
  );
  await page.route("**/api/v1/rewards/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") throw new Error(`Mutación inesperada: ${url.pathname}`);
    if (url.pathname === "/api/v1/rewards/summary/") {
      await route.fulfill({ status: 200, json: state.summary() });
    } else if (url.pathname === "/api/v1/rewards/transactions/") {
      const pageNumber = Number(url.searchParams.get("page"));
      state.transactionPages.push(pageNumber);
      await route.fulfill({ status: 200, json: state.transactions(pageNumber, baseURL) });
    } else if (url.pathname === "/api/v1/rewards/catalog/") {
      const catalog: RewardsCatalog = { period: PERIOD, results: [] };
      await route.fulfill({ status: 200, json: catalog });
    } else if (url.pathname === "/api/v1/rewards/redemptions/") {
      const results = state.movements().some((row) => row.redemption_id === redemption.id)
        ? [redemption]
        : [];
      const response: PaginatedResponse<Redemption> = {
        count: results.length,
        next: null,
        previous: null,
        results,
      };
      await route.fulfill({ status: 200, json: response });
    } else {
      throw new Error(`Ruta inesperada: ${url.pathname}`);
    }
  });
}

export async function openJourneyOperator(page: Page, state: JourneyState, baseURL: string) {
  await installNetworkGuards(page, baseURL);
  const metrics = await openOperatorQueue(page);
  // Conserva las respuestas de la fixture vecina y propaga la misma validación al autor.
  await page.route(
    `**/api/v1/operator/reports/${OPERATOR_REPORT_ID}/validate/**`,
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (
        request.method() !== "POST" ||
        url.searchParams.get("institution_id") !== ANDE_ID ||
        request.postDataBuffer() !== null
      ) {
        throw new Error("La validación debe enviar un POST vacío en el contexto de ANDE.");
      }
      state.validated = true;
      await route.fallback();
    },
  );
  return metrics;
}
