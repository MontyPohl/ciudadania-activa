"""Consulta y decisiones institucionales de reportes de Sprints 9 y 10."""

import uuid
from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_app.models import Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.permissions import IsInstitutionOperator
from apps.core.services import storage
from apps.reports_app.models import Report
from apps.reports_app.operator_serializers import (
    OperatorRejectInputSerializer,
    OperatorReportActionQuerySerializer,
    OperatorReportActionResponseSerializer,
    OperatorReportContextQuerySerializer,
    OperatorReportDetailSerializer,
    OperatorReportListQuerySerializer,
    OperatorReportListSerializer,
    OperatorValidateInputSerializer,
    ResolutionEvidenceInputSerializer,
    ResolutionEvidenceOutputSerializer,
)
from apps.reports_app.selectors import (
    attach_operator_historical_human_review,
    operator_report_detail,
    operator_report_list,
    operator_reports_for_institution,
)
from apps.reports_app.services import resolution_evidence as evidence_service
from apps.reports_app.services import validation

INSTITUTION_CONTEXT_FORBIDDEN = "No tenés acceso al contexto institucional solicitado."
INSTITUTION_CONTEXT_REQUIRED = (
    "Este campo es obligatorio cuando el operador tiene varias instituciones activas."
)
REPORT_PHOTO_UNAVAILABLE = "No se pudo obtener la fotografía en este momento."
REPORT_NOT_FOUND = "No se encontró el reporte solicitado."
INSTITUTION_OPERATOR_REQUIRED = "Se requiere ser operador de la institución solicitada."
INVALID_REJECTION_REASON = "El motivo de rechazo no pertenece al catálogo permitido."
EVIDENCE_UNAVAILABLE = "No se pudo completar la operación de evidencia en este momento."


class _OperatorInstitutionContextMixin:
    # ``IsOperatorOfReportInstitution`` no se usa aquí: su contrato general deniega
    # objetos sin institución, mientras S9-A autoriza el fallback de categoría solo
    # para estos GET. El queryset efectivo aplica después el scope por objeto.
    permission_classes = [IsInstitutionOperator]
    query_serializer_class = OperatorReportContextQuerySerializer

    def _params(self):
        if not hasattr(self, "_validated_operator_params"):
            serializer = self.query_serializer_class(data=self.request.query_params)
            serializer.is_valid(raise_exception=True)
            self._validated_operator_params = serializer.validated_data
        return self._validated_operator_params

    def _institution(self):
        if not hasattr(self, "_operator_institution"):
            self._operator_institution = _resolve_operator_institution(
                operator=self.request.user,
                requested_id=self._params().get("institution_id"),
            )
        return self._operator_institution

    @staticmethod
    def _private_no_store(response):
        response["Cache-Control"] = "private, no-store"
        return response


class OperatorReportListView(_OperatorInstitutionContextMixin, ListAPIView):
    serializer_class = OperatorReportListSerializer
    query_serializer_class = OperatorReportListQuerySerializer

    def get_queryset(self):
        return operator_report_list(
            institution=self._institution(),
            params=self._params(),
        )

    def list(self, request, *args, **kwargs):
        return self._private_no_store(super().list(request, *args, **kwargs))


class OperatorReportDetailView(_OperatorInstitutionContextMixin, RetrieveAPIView):
    serializer_class = OperatorReportDetailSerializer
    lookup_url_kwarg = "report_id"

    def get_queryset(self):
        return operator_report_detail(institution=self._institution())

    def get_object(self):
        report = super().get_object()
        return attach_operator_historical_human_review(report=report)

    def retrieve(self, request, *args, **kwargs):
        try:
            response = super().retrieve(request, *args, **kwargs)
        except storage.StorageError:
            response = Response(
                {"detail": REPORT_PHOTO_UNAVAILABLE},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return self._private_no_store(response)


class _OperatorReportActionView(APIView):
    """Adaptador HTTP común; toda la mutación de dominio permanece en S9-B1."""

    permission_classes = [IsAuthenticated]
    input_serializer_class = None

    def _params(self):
        serializer = OperatorReportActionQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _locked_operator_context(self):
        requested_id = self._params()["institution_id"]

        # Orden compartido con promote_user y extendido por S9-B1:
        # User -> InstitutionOperator -> Report -> Category -> Institution -> ledger.
        # No se sigue la relación ``institution`` aquí porque select_for_update()
        # bloquearía esa fila antes que Report y rompería el orden del agregado.
        user_model = get_user_model()
        try:
            operator = user_model.objects.select_for_update().get(pk=self.request.user.pk)
        except user_model.DoesNotExist:
            operator = None

        if (
            operator is None
            or not operator.is_active
            or getattr(operator, "role", None) != UserRole.OPERATOR
        ):
            raise PermissionDenied(
                {
                    "detail": INSTITUTION_OPERATOR_REQUIRED,
                    "code": "INSTITUTION_OPERATOR_REQUIRED",
                }
            )

        membership = (
            InstitutionOperator.objects.select_for_update()
            .only("institution_id")
            .filter(
                user=operator,
                institution_id=requested_id,
                active=True,
            )
            .first()
        )
        if membership is None:
            raise PermissionDenied(
                {
                    "detail": INSTITUTION_OPERATOR_REQUIRED,
                    "code": "INSTITUTION_OPERATOR_REQUIRED",
                }
            )
        return operator, Institution(pk=membership.institution_id)

    def _input(self):
        data = self.request.data
        serializer = self.input_serializer_class(data=data)
        reason_missing = self.input_serializer_class is OperatorRejectInputSerializer and (
            not isinstance(data, Mapping) or "reason" not in data
        )
        if serializer.is_valid():
            return serializer.validated_data

        errors = serializer.errors
        if reason_missing or (
            self.input_serializer_class is OperatorRejectInputSerializer and "reason" in errors
        ):
            return Response(
                {
                    "detail": errors,
                    "code": "INVALID_REJECTION_REASON",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        raise ValidationError(errors)

    def _visible_report(self, *, report_id, institution):
        visible = operator_reports_for_institution(institution=institution).filter(pk=report_id)
        if not visible.exists():
            raise NotFound({"detail": REPORT_NOT_FOUND, "code": "REPORT_NOT_FOUND"})

    @staticmethod
    def _response(report):
        report.refresh_from_db()
        response = Response(OperatorReportActionResponseSerializer(report).data)
        response["Cache-Control"] = "private, no-store"
        return response

    def _execute(self, *, report_id, operator, institution, data):
        raise NotImplementedError

    def post(self, request, report_id):
        try:
            with transaction.atomic():
                operator, institution = self._locked_operator_context()
                data = self._input()
                if isinstance(data, Response):
                    return data
                self._visible_report(report_id=report_id, institution=institution)
                result = self._execute(
                    report_id=report_id,
                    operator=operator,
                    institution=institution,
                    data=data,
                )
        except (validation.ReportInstitutionMismatch, Report.DoesNotExist):
            raise NotFound({"detail": REPORT_NOT_FOUND, "code": "REPORT_NOT_FOUND"}) from None
        except validation.InvalidRejectionReason:
            return Response(
                {"detail": INVALID_REJECTION_REASON, "code": "INVALID_REJECTION_REASON"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except validation.InactiveInstitution:
            return Response(
                {"detail": "La institución no está activa.", "code": "INSTITUTION_INACTIVE"},
                status=status.HTTP_409_CONFLICT,
            )
        except validation.ReportActionConflict as exc:
            return Response(
                {
                    "detail": "El estado actual del reporte no permite esta acción.",
                    "code": exc.code,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return self._response(result.report)


class OperatorReportValidateView(_OperatorReportActionView):
    input_serializer_class = OperatorValidateInputSerializer

    def _execute(self, *, report_id, operator, institution, data):
        return validation.validate_report(
            report_id=report_id,
            operator=operator,
            institution=institution,
        )


class OperatorReportRejectView(_OperatorReportActionView):
    input_serializer_class = OperatorRejectInputSerializer

    def _execute(self, *, report_id, operator, institution, data):
        return validation.reject_report(
            report_id=report_id,
            operator=operator,
            institution=institution,
            reason=data["reason"],
        )


class OperatorReportEvidenceView(APIView):
    permission_classes = [IsAuthenticated]

    def _params(self):
        serializer = OperatorReportActionQuerySerializer(data=self.request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, NotAuthenticated | AuthenticationFailed):
            response.data = {
                "detail": "Se requiere autenticación.",
                "code": "AUTHENTICATION_REQUIRED",
            }
        elif isinstance(exc, PermissionDenied):
            response.data = {
                "detail": INSTITUTION_OPERATOR_REQUIRED,
                "code": "INSTITUTION_OPERATOR_REQUIRED",
            }
        elif isinstance(exc, ValidationError):
            if not isinstance(response.data, dict) or "code" not in response.data:
                response.data = {"detail": response.data, "code": "INVALID_REQUEST"}
        return self._private(response)

    @staticmethod
    def _private(response):
        response["Cache-Control"] = "private, no-store"
        return response

    def get(self, request, report_id):
        institution_id = self._params()["institution_id"]
        try:
            report = evidence_service.get_operator_evidence_report(
                report_id=report_id,
                operator_id=request.user.pk,
                institution_id=institution_id,
            )
            evidences = report.resolution_evidences.order_by("-created_at", "-id")
            response = Response(
                ResolutionEvidenceOutputSerializer(
                    evidences,
                    many=True,
                    context={"request": request},
                ).data
            )
        except evidence_service.EvidenceOperatorRequired:
            response = Response(
                {"detail": INSTITUTION_OPERATOR_REQUIRED, "code": "INSTITUTION_OPERATOR_REQUIRED"},
                status=status.HTTP_403_FORBIDDEN,
            )
        except evidence_service.EvidenceNotFound:
            response = Response(
                {"detail": REPORT_NOT_FOUND, "code": "REPORT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except storage.StorageError:
            response = Response(
                {"detail": EVIDENCE_UNAVAILABLE, "code": "STORAGE_UNAVAILABLE"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return self._private(response)

    def post(self, request, report_id):
        params = self._params()
        serializer = ResolutionEvidenceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = evidence_service.create_resolution_evidence(
                report_id=report_id,
                operator_id=request.user.pk,
                institution_id=params["institution_id"],
                expected_cycle=serializer.validated_data["cycle"],
                idempotency_key=_idempotency_key(request),
                description=serializer.validated_data["description"],
                uploaded_file=serializer.validated_data["file"],
            )
            response = Response(
                ResolutionEvidenceOutputSerializer(
                    result.evidence,
                    context={"request": request},
                ).data,
                status=(status.HTTP_201_CREATED if result.created else status.HTTP_200_OK),
            )
        except evidence_service.EvidenceOperatorRequired:
            response = Response(
                {"detail": INSTITUTION_OPERATOR_REQUIRED, "code": "INSTITUTION_OPERATOR_REQUIRED"},
                status=status.HTTP_403_FORBIDDEN,
            )
        except evidence_service.EvidenceNotFound:
            response = Response(
                {"detail": REPORT_NOT_FOUND, "code": "REPORT_NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except (
            evidence_service.EvidenceStateConflict,
            evidence_service.StaleEvidenceCycle,
            evidence_service.EvidenceLimitReached,
            evidence_service.IdempotencyKeyReused,
        ) as exc:
            response = Response(
                {"detail": str(exc) or exc.code, "code": exc.code},
                status=status.HTTP_409_CONFLICT,
            )
        except evidence_service.InvalidEvidenceDescription as exc:
            response = Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except evidence_service.EvidenceTooLarge as exc:
            response = Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except evidence_service.UnsupportedEvidenceType as exc:
            response = Response(
                {"detail": str(exc), "code": exc.code},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        except storage.StorageError:
            response = Response(
                {"detail": EVIDENCE_UNAVAILABLE, "code": "STORAGE_UNAVAILABLE"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return self._private(response)


def _resolve_operator_institution(*, operator, requested_id):
    memberships = list(
        InstitutionOperator.objects.filter(user=operator, active=True)
        .select_related("institution")
        .order_by("institution__name", "institution_id")
    )

    if requested_id is not None:
        for membership in memberships:
            if membership.institution_id == requested_id:
                return membership.institution
        raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)

    if len(memberships) == 1:
        return memberships[0].institution
    if len(memberships) > 1:
        raise ValidationError({"institution_id": [INSTITUTION_CONTEXT_REQUIRED]})
    raise PermissionDenied(INSTITUTION_CONTEXT_FORBIDDEN)


def _idempotency_key(request):
    raw = request.headers.get("Idempotency-Key", "")
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError, AttributeError):
        raise ValidationError(
            {"detail": "Idempotency-Key debe ser un UUID.", "code": "INVALID_REQUEST"}
        ) from None
