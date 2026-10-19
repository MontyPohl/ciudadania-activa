import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def generate_voucher_code() -> str:
    """Genera 80 bits aleatorios; la unicidad la garantiza la base, sin reintentos."""
    value = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "CA-" + "-".join(value[index : index + 4] for index in range(0, 16, 4))
