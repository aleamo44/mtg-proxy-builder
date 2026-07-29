#!/usr/bin/env python3
"""Diagnostica di connessione a Cloudflare R2.

Legge le credenziali da .streamlit/secrets.toml (sezione [r2]) — mai
hardcoded — e prova a elencare il contenuto del bucket usando la stessa
identica configurazione del client impiegata dall'app (region 'auto',
firma s3v4, addressing path-style).

Uso: python3 test_r2_connection.py
"""

import sys
import tomllib
from pathlib import Path

import boto3
from botocore.config import Config

from cloud_enhancer import DEFAULT_BUCKET, build_endpoint_url

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"


def main() -> int:
    if not SECRETS_PATH.is_file():
        print(f"❌ File {SECRETS_PATH} non trovato.")
        print("   Crealo partendo da .streamlit/secrets.toml.example e")
        print("   compila la sezione [r2].")
        return 1

    with open(SECRETS_PATH, "rb") as handle:
        secrets = tomllib.load(handle)

    r2 = secrets.get("r2", {})
    raw_endpoint = str(r2.get("account_id") or r2.get("endpoint") or "").strip()
    access_key = str(r2.get("access_key_id", "")).strip()
    secret_key = str(r2.get("secret_access_key", "")).strip()
    bucket = str(r2.get("bucket", DEFAULT_BUCKET)).strip() or DEFAULT_BUCKET

    if not (raw_endpoint and access_key and secret_key):
        print("❌ Sezione [r2] incompleta nei secrets: servono account_id")
        print("   (URL endpoint o account ID), access_key_id e")
        print("   secret_access_key.")
        return 1

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
