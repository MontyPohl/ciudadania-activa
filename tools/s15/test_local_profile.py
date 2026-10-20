"""Fallo cerrado del perfil antes de iniciar servidores o abrir conexiones."""

import unittest

from tools.s15.local_profile import ProfileError, validate_environment


def environment():
    return {
        "S15_LOCAL_PROFILE": "isolated-measurement-v1",
        "DJANGO_SETTINGS_MODULE": "tools.s15.measurement_settings",
        "DATABASE_URL": "postgis://ciudadania:s15-local-database-only@ca-s15-db:5432/ciudadania_s15",
        "REDIS_URL": "redis://ca-s15-redis:6379/0",
        "SUPABASE_URL": "http://127.0.0.1:18515",
        "SUPABASE_STORAGE_BUCKET_PRIVATE": "s15-private",
        "SUPABASE_STORAGE_BUCKET_REPORTS": "s15-reports",
        "SUPABASE_SERVICE_ROLE_KEY": "s15-synthetic-storage-measurement-only",
        "DOCUMENT_HMAC_SECRET": "s15-synthetic-document-measurement-only",
        "DJANGO_SECRET_KEY": "s15-synthetic-django-measurement-key-only",
        "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1,testserver",
        "AI_CLASSIFICATION_ENABLED": "false",
        "AI_PROVIDER": "",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "S11_EXPERIMENTS_ENABLED": "false",
        "S11_LOCAL_STORAGE_ENABLED": "false",
        "BREVO_API_URL": "http://127.0.0.1:18516/v3/smtp/email",
        "BREVO_API_KEY": "s15-synthetic-mail-measurement-only",
    }


class ProfileTests(unittest.TestCase):
    def test_accepts_only_dedicated_profile_and_minimizes_receipt(self):
        result = validate_environment(environment())
        self.assertEqual(result["mail"], "local-substitute")
        self.assertEqual(result["database"], "ciudadania_s15")
        self.assertNotIn("KEY", str(result))

    def test_rejects_missing_or_overridden_guard_inputs(self):
        for key in environment():
            with self.subTest(key=key):
                values = environment()
                if key == "BREVO_API_KEY":
                    values[key] = "real-provider-key"
                else:
                    values.pop(key)
                with self.assertRaises(ProfileError):
                    validate_environment(values)

    def test_rejects_real_destinations_keys_and_other_sprint(self):
        for key, value in [
            ("DATABASE_URL", "postgis://ciudadania:key@remote.test:5432/ciudadania"),
            ("REDIS_URL", "redis://ca-s14-redis:6379/4"),
            ("SUPABASE_URL", "https://project.supabase.co"),
            ("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email"),
            ("DJANGO_ALLOWED_HOSTS", "*"),
            ("AI_CLASSIFICATION_ENABLED", "true"),
            ("OPENAI_API_KEY", "key-must-not-appear-in-errors"),
            ("CELERY_BROKER_URL", "redis://another-project:6379/0"),
        ]:
            with self.subTest(key=key):
                values = environment()
                values[key] = value
                with self.assertRaises(ProfileError) as raised:
                    validate_environment(values)
                self.assertNotIn(value, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
