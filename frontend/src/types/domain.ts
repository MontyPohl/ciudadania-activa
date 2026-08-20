/**
 * Tipos espejo del modelo de datos del backend (Sprint 2).
 *
 * Reflejan las entidades y enumeraciones del Anexo A tal como quedaron
 * implementadas en los modelos Django del backend, para que los sprints siguientes
 * trabajen contra contratos estables. No contienen lógica de negocio ni valores
 * derivados: los saldos, el ranking y los puntos se calculan en el backend a
 * partir del ledger.
 *
 * Convenciones: los identificadores son UUID en texto y las marcas temporales,
 * cadenas ISO-8601 tal como las serializa Django REST Framework.
 */

export type UUID = string;
export type ISODateTime = string;

/* -------------------------------------------------------------------------- */
/* Enumeraciones                                                              */
/* -------------------------------------------------------------------------- */

export type UserRole = "citizen" | "operator" | "admin";

export type VerificationStatus = "PENDIENTE" | "APROBADO" | "RECHAZADO";

export type RankingVisibility = "FULL_NAME" | "FIRST_INITIAL" | "ALIAS" | "PRIVATE";

/** No existe un estado `VALIDADO`: la validación institucional es un evento. */
export type ReportStatus =
  "CREADO" | "CLASIFICADO" | "ASIGNADO" | "EN_PROCESO" | "RESUELTO" | "CERRADO" | "RECHAZADO";

export type ReportPriority = "BAJA" | "MEDIA" | "ALTA";

export type ReportSource = "platform" | "telegram" | "x_mock";

export type RejectionReason =
  "fuera_de_competencia" | "duplicado" | "informacion_insuficiente" | "contenido_inapropiado";

export type MediaType = "FOTO";

export type ActorType = "human" | "ai" | "system";

export type RewardTxStatus = "PENDIENTE" | "ACREDITADO" | "ANULADO" | "REVERTIDO";

export type RewardEventType =
  | "REPORT_CREATED"
  | "CONFIRMATION_CREATED"
  | "VALIDATION_BONUS"
  | "RESOLUTION_BONUS"
  | "REDEMPTION"
  | "REVERSAL";

export type NotificationType =
  | "VERIFICACION_APROBADA"
  | "VERIFICACION_RECHAZADA"
  | "REPORTE_CREADO"
  | "VALIDACION_INSTITUCIONAL"
  | "REPORTE_RESUELTO"
  | "REPORTE_RECHAZADO"
  | "CANJE_REALIZADO"
  | "RECOMPENSA_DISPONIBLE";

export type NotificationChannel = "EMAIL";

export type NotificationStatus = "PENDIENTE" | "ENVIADO" | "FALLIDO";

/* -------------------------------------------------------------------------- */
/* Entidades                                                                  */
/* -------------------------------------------------------------------------- */

interface Timestamped {
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export interface User {
  id: UUID;
  email: string;
  fullName: string;
  role: UserRole;
  verificationStatus: VerificationStatus;
  dateJoined: ISODateTime;
}

/** `documentHmac` y `verificationSelfieUrl` nunca se serializan al cliente. */
export interface CitizenProfile extends Timestamped {
  id: UUID;
  userId: UUID;
  verifiedAt: ISODateTime | null;
  rankingVisibility: RankingVisibility;
  rankingAlias: string;
}

export interface Institution extends Timestamped {
  id: UUID;
  name: string;
  slug: string;
  contactEmail: string;
  slaHours: number | null;
  isActive: boolean;
}

export interface Category extends Timestamped {
  id: UUID;
  name: string;
  slug: string;
  defaultInstitutionId: UUID;
  defaultPriority: ReportPriority;
  isActive: boolean;
}

export interface InstitutionOperator extends Timestamped {
  id: UUID;
  userId: UUID;
  institutionId: UUID;
  active: boolean;
}

export interface AuditLog extends Timestamped {
  id: UUID;
  actorUserId: UUID | null;
  actorType: ActorType;
  action: string;
  payload: Record<string, unknown>;
}

/** Punto en WGS84 (SRID 4326). */
export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface Report extends Timestamped {
  id: UUID;
  citizenId: UUID | null;
  categoryId: UUID;
  institutionId: UUID | null;
  description: string;
  status: ReportStatus;
  priority: ReportPriority;
  source: ReportSource;
  location: GeoPoint;
  validatedAt: ISODateTime | null;
  validatedById: UUID | null;
  resolvedAt: ISODateTime | null;
  rejectionReason: RejectionReason | "";
}

export interface ReportMedia extends Timestamped {
  id: UUID;
  reportId: UUID;
  fileUrl: string;
  fileHash: string;
  mediaType: MediaType;
  uploadedById: UUID | null;
}

export interface ReportStatusHistory extends Timestamped {
  id: UUID;
  reportId: UUID;
  previousStatus: ReportStatus | "";
  newStatus: ReportStatus;
  changedById: UUID | null;
  reason: string;
}

export interface ResolutionEvidence extends Timestamped {
  id: UUID;
  reportId: UUID;
  description: string;
  evidenceUrl: string;
  uploadedById: UUID | null;
}

/**
 * El instante de la confirmación es `createdAt` del modelo base: no existe una
 * columna `confirmed_at` en la base de datos (decisión D2-10).
 */
export interface ReportConfirmation extends Timestamped {
  id: UUID;
  reportId: UUID;
  citizenId: UUID;
}

export interface AiClassification extends Timestamped {
  id: UUID;
  reportId: UUID;
  suggestedCategoryId: UUID | null;
  suggestedInstitutionId: UUID | null;
  suggestedPriority: ReportPriority | "";
  confidence: number | null;
  inconsistencyFlag: boolean;
}

/**
 * Fila del ledger. `impactDelta` y `creditDelta` son inmutables: solo cambian el
 * estado, la institución y las marcas temporales.
 */
export interface RewardTransaction extends Timestamped {
  id: UUID;
  userId: UUID;
  reportId: UUID | null;
  confirmationId: UUID | null;
  institutionId: UUID | null;
  eventType: RewardEventType;
  impactDelta: number;
  creditDelta: number;
  status: RewardTxStatus;
  relatedTransactionId: UUID | null;
  creditedAt: ISODateTime | null;
  voidedAt: ISODateTime | null;
  reversedAt: ISODateTime | null;
}

export interface Reward extends Timestamped {
  id: UUID;
  institutionId: UUID;
  name: string;
  description: string;
  creditCost: number;
  active: boolean;
  simulated: boolean;
}

export interface RewardRedemption extends Timestamped {
  id: UUID;
  userId: UUID;
  rewardId: UUID;
  code: string;
  creditsSpent: number;
}

export interface Notification extends Timestamped {
  id: UUID;
  userId: UUID;
  reportId: UUID | null;
  notificationType: NotificationType;
  channel: NotificationChannel;
  status: NotificationStatus;
}

export interface TelegramIngestedMessage extends Timestamped {
  id: UUID;
  telegramChatId: string;
  senderAlias: string;
  rawText: string;
  reportId: UUID | null;
}

export interface XMockMessage extends Timestamped {
  id: UUID;
  sourceHandle: string;
  rawText: string;
  simulatedAt: ISODateTime;
  reportId: UUID | null;
}
