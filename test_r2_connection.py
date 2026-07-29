#!/usr/bin/env python3
"""Diagnostica di connessione a Cloudflare R2.

Legge le credenziali da .streamlit/secrets.toml (sezione [r2]) — mai
hardcoded — e prova a elencare il contenuto del bucket usando la stessa
identica configurazione del client impiegata dall'app (region 'auto',
firma s3v4, addressing path-style).

In alternativa al file secrets.toml, le credenziali possono essere fornite
tramite le variabili d'ambiente R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY e (opzionale) R2_BUCKET.

Uso: python3 test_r2_connection.py
"""

import os
import sys
import tomllib
from pathlib import Path

import boto3
from botocore.config import Config

from cloud_enhancer import DEFAULT_BUCKET, build_endpoint_url

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"


def _load_r2_credentials() -> dict:
    """Credenziali dalla sezione [r2] dei secrets o dalle variabili d'ambiente."""
    r2: dict = {}
    if SECRETS_PATH.is_file():
        with open(SECRETS_PATH, "rb") as handle:
            r2 = tomllib.load(handle).get("r2", {})
        print(f"Credenziali lette da {SECRETS_PATH}")
    elif os.environ.get("R2_ACCESS_KEY_ID"):
        r2 = {
            "account_id": os.environ.get("R2_ACCOUNT_ID", ""),
            "access_key_id": os.environ.get("R2_ACCESS_KEY_ID", ""),
            "secret_access_key": os.environ.get("R2_SECRET_ACCESS_KEY", ""),
            "bucket": os.environ.get("R2_BUCKET", DEFAULT_BUCKET),
        }
        print("Credenziali lette dalle variabili d'ambiente R2_*")
    return r2


def main() -> int:
    r2 = _load_r2_credentials()
    raw_endpoint = str(r2.get("account_id") or r2.get("endpoint") or "").strip()
    access_key = str(r2.get("access_key_id", "")).strip()
    secret_key = str(r2.get("secret_access_key", "")).strip()
    bucket = str(r2.get("bucket", DEFAULT_BUCKET)).strip() or DEFAULT_BUCKET

    if not (raw_endpoint and access_key and secret_key):
        print("❌ Credenziali R2 non trovate o incomplete.")
        print(f"   Opzione 1: crea {SECRETS_PATH} partendo da")
        print("   .streamlit/secrets.toml.example e compila la sezione [r2].")
        print("   Opzione 2: imposta le variabili d'ambiente R2_ACCOUNT_ID,")
        print("   R2_ACCESS_KEY_ID e R2_SECRET_ACCESS_KEY.")
        return 1

    if len(access_key) != 32:
        print(
            f"⚠️ Attenzione: l'Access Key ID è lungo {len(access_key)} "
            "caratteri, ma R2 si aspetta 32. Probabilmente hai copiato il "
            "valore sbagliato (es. un API Token invece della chiave S3)."
        )

    endpoint_url = build_endpoint_url(raw_endpoint)
    print(f"Testing endpoint: {endpoint_url}")
    print(f"Bucket: {bucket}")

    s3 = boto3.client(
        service_name="s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",  # Obbligatorio per R2
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # Evita "Invalid endpoint"
        ),
    )

    try:
        response = s3.list_objects_v2(Bucket=bucket)
        contents = response.get("Contents", [])
        print("✅ CONNESSI CON SUCCESSO A CLOUDFLARE R2!")
        print(f"Oggetti trovati nel bucket: {len(contents)}")
        for obj in contents[:10]:
            print(f"  - {obj['Key']} ({obj['Size'] // 1024} KB)")
        if len(contents) > 10:
            print(f"  ... e altri {len(contents) - 10} oggetti.")
        return 0
    except Exception as exc:
        print("❌ ERRORE S3:")
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
