import { render, screen } from "@testing-library/react";

import { useSession } from "@/features/auth/useSession";
import { PublicReportDetail } from "@/features/map/PublicReportDetail";
import { OperatorReportDetail } from "@/features/reports/operator/OperatorReportDetail";
import { RewardCard } from "@/features/rewards/RewardCard";
import {
  getOperatorEvidence,
  getOperatorReport,
  getOperatorReportHistory,
} from "@/lib/api/operatorReports";
import { getPublicReport } from "@/lib/api/publicReports";
import { publicReportDetail } from "@/test/fixtures";
import type { CatalogEntry, OperatorReportDetail as OperatorDetail } from "@/types/api";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/lib/api/publicReports", async (original) => {
  const actual = await original<typeof import("@/lib/api/publicReports")>();
  return { ...actual, getPublicReport: vi.fn() };
});
vi.mock("@/lib/api/operatorReports", async (original) => {
  const actual = await original<typeof import("@/lib/api/operatorReports")>();
  return {
    ...actual,
    getOperatorReport: vi.fn(),
    getOperatorReportHistory: vi.fn(),
    getOperatorEvidence: vi.fn(),
    createOperatorEvidence: vi.fn(),
    transitionOperatorReport: vi.fn(),
    validateOperatorReport: vi.fn(),
    rejectOperatorReport: vi.fn(),
  };
});

const municipal = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Municipalidad",
  slug: "municipalidad",
};
const ande = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "ANDE",
  slug: "ande",
};
const operatorDetail: OperatorDetail = {
  id: "33333333-3333-4333-8333-333333333333",
  status: "CREADO",
  cycle: 0,
  priority: "MEDIA",
  category: { id: "44444444-4444-4444-8444-444444444444", name: "Baches", slug: "baches" },
  institution: null,
  effective_read_institution: municipal,
  created_at: "2026-09-01T10:00:00Z",
  description: "Bache profundo",
  validated_at: null,
  resolved_at: null,
  closed_at: null,
  latitude: -25.3,
  longitude: -57.6,
  photo_signed_url: null,
  confirmation_count: 2,
  ai_classification: {
    suggested_category: {
      id: "44444444-4444-4444-8444-444444444444",
      name: "Baches",
      slug: "baches",
    },
    suggested_institution: ande,
    suggested_priority: "ALTA",
    confidence: 0.9,
    inconsistency_flag: false,
    justification: "Puede corresponder a ANDE.",
    model_name: "fake",
    requires_human_review: true,
    created_at: "2026-09-01T10:00:00Z",
  },
};
const catalogEntry: CatalogEntry = {
  institution: ande,
  reward: {
    id: "33333333-3333-4333-8333-333333333333",
    name: "Vale simulado",
    description: "Recompensa de prueba",
    credit_cost: 1000,
    simulated: true,
  },
  balance: 1130,
  can_redeem: true,
  blocked_reason: null,
  missing_credits: 0,
  redemption_this_month: null,
};

const payloads = ["<script>window.__xss=1</script>", '<img src=x onerror="window.__xss=1">'];
const components = ["PublicReportDetail", "OperatorReportDetail", "RewardCard"] as const;

describe("texto ciudadano no confiable", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useSession).mockReturnValue({
      status: "unauthenticated",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    vi.mocked(getOperatorReportHistory).mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          previous_status: "",
          new_status: "CREADO",
          cycle: 0,
          reason: "",
          created_at: "2026-09-01T10:00:00Z",
        },
      ],
    });
    vi.mocked(getOperatorEvidence).mockResolvedValue([]);
  });

  afterEach(() => {
    delete (window as Window & { __xss?: number }).__xss;
  });

  it.each(components.flatMap((component) => payloads.map((text) => ({ component, text }))))(
    "no interpreta HTML de texto ciudadano",
    async ({ component, text }) => {
      vi.mocked(getPublicReport).mockResolvedValue(publicReportDetail({ description: text }));
      vi.mocked(getOperatorReport).mockResolvedValue({ ...operatorDetail, description: text });

      if (component === "OperatorReportDetail") {
        vi.mocked(useSession).mockReturnValue({
          status: "authenticated",
          user: {
            id: "55555555-5555-4555-8555-555555555555",
            email: "operator@example.test",
            full_name: "Operador",
            role: "operator",
            verification_status: "PENDIENTE",
            is_verified_citizen: false,
            institutions: [municipal, ande],
          },
          signInWithGoogle: vi.fn(),
          signOut: vi.fn(),
        });
      }

      const { container } = render(
        component === "PublicReportDetail" ? (
          <PublicReportDetail id={publicReportDetail().id} />
        ) : component === "OperatorReportDetail" ? (
          <OperatorReportDetail id={operatorDetail.id} initialInstitutionId={municipal.id} />
        ) : (
          <RewardCard
            entry={{ ...catalogEntry, reward: { ...catalogEntry.reward, name: text } }}
            period="2026-10"
            onRedeem={vi.fn()}
          />
        ),
      );

      await screen.findByText(text);
      expect(screen.getByText(text)).toBeInTheDocument();
      expect(container.querySelector("script, img[src='x']")).toBeNull();
      expect((window as Window & { __xss?: number }).__xss).toBeUndefined();
    },
  );
});
