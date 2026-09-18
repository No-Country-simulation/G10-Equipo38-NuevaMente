---
name: oci-always-free-storage
description: Integrates Oracle Cloud Infrastructure (OCI) Object Storage using oci-sdk for Python under strict Always Free ($0.00 cost) rules, targeting bucket nuevamente-contenidos-educativos with an automatic local mock fallback for offline or zero-credential development. Use when configuring cloud storage, persisting generated content, or generating artifact download links.
---

# OCI Always Free Storage

## Overview

A robust cloud persistence skill ensuring seamless, production-grade integration with **Oracle Cloud Infrastructure (OCI) Object Storage** via the official Python SDK (`oci`). 

Designed under a strict **Zero-Cost ($0.00) Always Free Policy**, it securely stores generated pedagogical content, flashcards (`.apkg`), quizzes, and summaries in the target bucket `nuevamente-contenidos-educativos`. It includes an automatic **Local Mock Fallback** provider that allows local development, CI testing, and evaluation without needing OCI credentials or incurring any cost.

## When to Use

- Persisting generated educational outputs, JSON schemas, Anki decks, or PDFs to the cloud.
- Interacting with OCI Object Storage via `oci.object_storage.ObjectStorageClient`.
- Setting up or verifying the bucket `nuevamente-contenidos-educativos`.
- Developing locally or running tests when OCI API keys are not present (automatic mock fallback).
- Monitoring storage usage to guarantee strict $0.00 cost compliance.

---

## 1. OCI Always Free $0.00 Cost Governance

Oracle Cloud Infrastructure provides an industry-leading Always Free tier. To maintain zero costs indefinitely:

| Metric | Always Free Limit | NuevaMente Usage Pattern | Cost Impact |
|---|---|---|---|
| **Standard Storage** | 10 GB per tenancy | Compressed JSON/Markdown (~50KB each) < 200MB | **$0.00** |
| **Archive Storage** | 10 GB per tenancy | Optional cold archival | **$0.00** |
| **API Requests** | 50,000 / month | < 2,000 requests during demo & testing | **$0.00** |
| **Outbound Transfer** | 10 TB / month | Web UI downloads < 500MB | **$0.00** |

### Golden Rules for Zero-Cost Operations

1. **Bucket Tier**: Always specify `storage_tier="Standard"` (or `Archive`). Never select ephemeral or premium tiers.
2. **Lifecycle Policies**: Automatically expire or delete temporary evaluation runs older than 30 days if storage approaches limits.
3. **Payload Compression**: Store educational packages as compressed JSON or `.apkg` archives.
4. **Idempotent Retries**: Implement exponential backoff with a maximum of 3 retries to prevent runaway API call counts.

---

## 2. Python Architecture: OCI Client with Local Fallback

### Core Storage Manager (`app/storage/oci_storage.py`)

```python
import io
import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("nuevamente.storage")

DEFAULT_BUCKET_NAME = "nuevamente-contenidos-educativos"
LOCAL_MOCK_DIR = Path(".data/oci_mock_storage")

class StorageProvider:
    """Abstract interface for educational content storage."""
    def upload_content(self, object_name: str, content: str | bytes, content_type: str = "application/json") -> str:
        raise NotImplementedError
        
    def get_content(self, object_name: str) -> bytes:
        raise NotImplementedError

    def get_content_as_text(self, object_name: str) -> str:
        """Helper to retrieve content decoded as UTF-8 text."""
        return self.get_content(object_name).decode("utf-8")
        
    def list_contents(self, prefix: str = "") -> List[str]:
        raise NotImplementedError

class LocalMockStorageProvider(StorageProvider):
    """Fallback provider when OCI credentials are not configured or MOCK_OCI=1."""
    
    def __init__(self, bucket_name: str = DEFAULT_BUCKET_NAME):
        self.bucket_dir = LOCAL_MOCK_DIR / bucket_name
        self.bucket_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Using LocalMockStorageProvider at {self.bucket_dir.resolve()} (Zero-Cost Local Mode)")

    def upload_content(self, object_name: str, content: str | bytes, content_type: str = "application/json") -> str:
        file_path = self.bucket_dir / object_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if isinstance(content, str):
            file_path.write_text(content, encoding="utf-8")
        else:
            file_path.write_bytes(content)
            
        return f"mock://{self.bucket_dir.name}/{object_name}"

    def get_content(self, object_name: str) -> bytes:
        file_path = self.bucket_dir / object_name
        if not file_path.exists():
            raise FileNotFoundError(f"Mock object {object_name} not found.")
        return file_path.read_bytes()

    def list_contents(self, prefix: str = "") -> List[str]:
        items = []
        for p in self.bucket_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.bucket_dir)).replace("\\", "/")
                if rel.startswith(prefix):
                    items.append(rel)
        return items

class OCIObjectStorageProvider(StorageProvider):
    """Production provider connecting to Oracle Cloud Infrastructure Object Storage."""
    
    def __init__(self, bucket_name: str = DEFAULT_BUCKET_NAME, config_file: Optional[str] = None):
        self.bucket_name = bucket_name
        
        # Load OCI configuration from default file or environment variables
        try:
            import oci
            if config_file and os.path.exists(config_file):
                self.config = oci.config.from_file(config_file)
            elif os.path.exists(os.path.expanduser("~/.oci/config")):
                self.config = oci.config.from_file()
            else:
                # Fallback to individual environment variables
                self.config = {
                    "user": os.environ["OCI_USER_OCID"],
                    "key_file": os.environ["OCI_KEY_FILE"],
                    "fingerprint": os.environ["OCI_FINGERPRINT"],
                    "tenancy": os.environ["OCI_TENANCY_OCID"],
                    "region": os.environ.get("OCI_REGION", "us-ashburn-1")
                }
            
            self.client = oci.object_storage.ObjectStorageClient(self.config)
            self.namespace = self.client.get_namespace().data
            self._ensure_bucket_exists()
            logger.info(f"Connected to OCI Object Storage (Tenancy: {self.config['tenancy']}, Bucket: {self.bucket_name})")
        except Exception as e:
            logger.warning(f"Failed to initialize OCI Object Storage client: {e}. Falling back to LocalMockStorageProvider.")
            raise

    def _ensure_bucket_exists(self):
        import oci
        try:
            self.client.get_bucket(self.namespace, self.bucket_name)
        except oci.exceptions.ServiceError as e:
            if e.status == 404:
                request = oci.object_storage.models.CreateBucketDetails(
                    name=self.bucket_name,
                    compartment_id=self.config["tenancy"],
                    storage_tier=oci.object_storage.models.CreateBucketDetails.STORAGE_TIER_STANDARD
                )
                self.client.create_bucket(self.namespace, request)
                logger.info(f"Created OCI Bucket {self.bucket_name} in Standard Always Free tier.")
            else:
                raise

    def upload_content(self, object_name: str, content: str | bytes, content_type: str = "application/json") -> str:
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.client.put_object(
            namespace_name=self.namespace,
            bucket_name=self.bucket_name,
            object_name=object_name,
            put_object_body=body,
            content_type=content_type
        )
        return f"oci://{self.bucket_name}/{object_name}"

    def get_content(self, object_name: str) -> bytes:
        response = self.client.get_object(self.namespace, self.bucket_name, object_name)
        return response.data.content

    def list_contents(self, prefix: str = "") -> List[str]:
        response = self.client.list_objects(self.namespace, self.bucket_name, prefix=prefix)
        return [obj.name for obj in response.data.objects]

def get_storage_provider() -> StorageProvider:
    """Factory creating OCIObjectStorageProvider or falling back to LocalMockStorageProvider."""
    force_mock = os.getenv("MOCK_OCI", "0") in ("1", "true", "True")
    if force_mock:
        return LocalMockStorageProvider()
        
    try:
        return OCIObjectStorageProvider()
    except Exception:
        return LocalMockStorageProvider()
```

---

## 3. Storage Hierarchy in Bucket

Organize educational content deterministically by session or date:

```
nuevamente-contenidos-educativos/
├── outputs/
│   └── 2026-09-16/
│       ├── tutorial_fintech_junior_001.json
│       ├── quiz_salud_executive_002.json
│       └── flashcards_ecommerce_beginner_003.json
├── decks/
│   └── flashcards_ecommerce_beginner_003.apkg
└── source_documents/
    └── manual_tecnico_api_v1.pdf
```

---

## 4. OCI VM Compute Always Free Deployment (Differential)

To run NuevaMente continuously on Oracle Cloud at $0.00:
1. **Compute Instance**: Launch an `Ampere A1 Compute` instance (up to 4 OCPUs and 24 GB RAM Always Free) or `VM.Standard.E2.1.Micro` (1 OCPU, 1 GB RAM).
2. **OS**: Oracle Linux 9 or Ubuntu 24.04 LTS.
3. **Systemd Service**: Run Streamlit as a persistent daemon:
```ini
[Unit]
Description=NuevaMente Streamlit Application
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/G10-Equipo38-NuevaMente
ExecStart=/home/opc/venv/bin/streamlit run app/ui/app.py --server.port 8501 --server.address 0.0.0.0
Restart=always
Environment=MOCK_OCI=0

[Install]
WantedBy=multi-user.target
```
4. **VCN Ingress Rules**: Allow TCP traffic on port `8501` from the public internet.
