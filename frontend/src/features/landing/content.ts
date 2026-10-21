// Institution mapping from backend/apps/admin_app/catalogs.py. Names come from the public API.
export const categoryInstitutions: Record<string, string> = {
  baches: "Municipalidad de Asunción",
  "basura-acumulada": "Municipalidad de Asunción",
  semaforos: "Municipalidad de Asunción",
  escombros: "Municipalidad de Asunción",
  "arbol-caido": "Municipalidad de Asunción",
  otros: "Municipalidad de Asunción",
  "alumbrado-publico": "ANDE",
  "cables-expuestos-riesgo-electrico": "ANDE",
  "agua-potable-perdidas": "ESSAP",
  "drenaje-desague-cloaca": "ESSAP",
};

export const citizenSteps = [
  ["Ingresá con Google", "Usá tu cuenta de Google para iniciar sesión."],
  [
    "Verificá tu identidad",
    "Enviá tu número de cédula y una selfie sosteniendo tu cédula. Un administrador revisa la solicitud manualmente antes de aprobarla o rechazarla.",
  ],
  [
    "Reportá con foto y ubicación",
    "Con tu identidad aprobada, elegí una categoría, describí el problema y adjuntá una foto y su ubicación. La foto será pública desde la validación institucional: evitá caras, patentes y datos personales.",
  ],
  [
    "Otros ciudadanos confirman",
    "Ciudadanos verificados pueden confirmar reportes activos de otras personas. No podés confirmar tu propio reporte ni confirmar dos veces el mismo.",
  ],
  [
    "La institución gestiona",
    "La institución valida o rechaza el reporte. Si lo atiende, puede pasarlo a En proceso y debe cargar evidencia para marcarlo como Resuelto.",
  ],
  [
    "Consultá tus créditos y vales",
    "Los incentivos quedan pendientes hasta la validación institucional. Los créditos acreditados se consultan por institución y se pueden canjear por vales simulados del catálogo disponible.",
  ],
];
export const institutionSteps = [
  [
    "Acceso administrativo",
    "Las cuentas institucionales las crea el administrador y se vinculan a una institución. No hay registro público de operadores.",
  ],
  [
    "Validar y atender",
    "Desde la bandeja, los operadores revisan los reportes de su institución, los validan o rechazan y actualizan su estado de atención.",
  ],
  [
    "Cargar evidencia y resolver",
    "Durante En proceso se carga la evidencia de resolución. Para pasar a Resuelto se exige al menos una evidencia del ciclo actual.",
  ],
];
export const privacyStatements = [
  [
    "Identidad fuera del mapa",
    "El listado y el detalle públicos no muestran el nombre, correo, número de cédula ni selfie del autor.",
  ],
  [
    "Ubicación y fotos públicas",
    "El mapa muestra coordenadas redondeadas. La foto del reporte se publica desde la validación institucional; las evidencias son públicas en Resuelto y Cerrado.",
  ],
  [
    "Estadísticas con resguardos",
    "Algunas celdas de las estadísticas se ocultan para proteger la privacidad. Una cifra oculta se muestra como un guion, no como cero.",
  ],
];
export const faqs = [
  [
    "¿Necesito registrarme para ver el mapa?",
    "No. El mapa, los reportes públicos y las estadísticas se pueden consultar sin iniciar sesión. Para crear o confirmar reportes necesitás una cuenta ciudadana con identidad aprobada.",
  ],
  [
    "¿Quién revisa mi cédula y mi selfie?",
    "Un administrador revisa manualmente tu solicitud. El número de cédula se almacena como un código HMAC; la selfie se guarda en almacenamiento privado y puede contener el número visible en la imagen.",
  ],
  [
    "¿Se publica mi nombre?",
    "El mapa y los reportes públicos no muestran tu nombre. El ranking tiene una preferencia independiente en tu perfil: podés aparecer como Usuario privado o elegir la forma pública de tu nombre.",
  ],
  [
    "¿Confirmar un reporte lo resuelve automáticamente?",
    "No. La confirmación ciudadana y la gestión institucional son pasos distintos. La institución actualiza el estado y necesita evidencia para resolver.",
  ],
  [
    "¿Los vales tienen valor comercial?",
    "Los vales de esta versión son simulados. El canje cuesta 1.000 créditos de la institución correspondiente y se permite un canje por institución y mes, sujeto al saldo y al catálogo activo.",
  ],
  [
    "¿Por qué algunas estadísticas muestran un guion?",
    "Algunas cifras se suprimen cuando hay menos de 5 casos, incluso cero. También puede ocultarse otra celda para evitar que se deduzca una cifra protegida a partir de los totales.",
  ],
];
