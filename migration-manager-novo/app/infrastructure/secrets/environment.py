"""Resolve referencias de credenciais por variaveis de ambiente."""
from __future__ import annotations

import os
import re


class EnvironmentSecretResolver:
    """Mapeia ``ms2_database_source`` para ``SECRET_MS2_DATABASE_SOURCE_*``."""

    @staticmethod
    def credentials(reference: str | None) -> tuple[str, str]:
        if not reference:
            raise ValueError("credentials_ref e obrigatorio para conexoes PostgreSQL reais")
        normalized = re.sub(r"[^A-Za-z0-9]", "_", reference).upper().strip("_")
        prefix = f"SECRET_{normalized}"
        username, password = os.getenv(f"{prefix}_USER"), os.getenv(f"{prefix}_PASSWORD")
        if not username or not password:
            raise RuntimeError(f"Segredo ausente: defina {prefix}_USER e {prefix}_PASSWORD")
        return username, password
