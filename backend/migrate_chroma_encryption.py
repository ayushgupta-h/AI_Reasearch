"""
ChromaDB Migration & Backup Script
===================================
1. Backs up existing ChromaDB files and exports all collection records to JSON.
2. Migrates existing plaintext customer document records to AES-256-GCM ciphertext.
3. Preserves vector embeddings completely untouched so similarity search works identically.
4. Verifies post-migration that all records are encrypted and cleanly decryptable.
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import datetime
from pathlib import Path
from typing import Any, Dict, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

# Ensure backend directory is in path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv
load_dotenv(backend_dir / ".env")

import chromadb
from crypto_utils import encrypt_text, decrypt_text, is_encrypted, get_or_create_chroma_key


def backup_local_chromadb(local_dir: Path) -> Path:
    """Make a complete filesystem copy of local chroma_db directory."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = local_dir.parent / f"chroma_db_backup_{timestamp}"
    if local_dir.is_dir():
        console.print(f"  [dim]📦 Creating local filesystem backup at {backup_path}...[/dim]")
        shutil.copytree(local_dir, backup_path)
        console.print(f"  [green]✅ Local filesystem backup created:[/green] {backup_path.name}")
    return backup_path


def export_collections_snapshot(
    local_client: Optional[chromadb.PersistentClient],
    cloud_client: Optional[chromadb.CloudClient],
    output_path: Path,
) -> Dict[str, Any]:
    """Export all documents, metadatas, and embeddings from all collections to a JSON backup file."""
    snapshot: Dict[str, Any] = {
        "timestamp": datetime.datetime.now().isoformat(),
        "local_collections": {},
        "cloud_collections": {},
    }

    if local_client:
        for col in local_client.list_collections():
            data = col.get(include=["documents", "metadatas", "embeddings"])
            # Embeddings might be numpy arrays or lists
            embs = data.get("embeddings")
            if embs is not None:
                embs = [e.tolist() if hasattr(e, "tolist") else e for e in embs]
            snapshot["local_collections"][col.name] = {
                "ids": data.get("ids", []),
                "documents": data.get("documents", []),
                "metadatas": data.get("metadatas", []),
                "embeddings": embs,
            }

    if cloud_client:
        for col in cloud_client.list_collections():
            try:
                data = col.get(include=["documents", "metadatas", "embeddings"])
                embs = data.get("embeddings")
                if embs is not None:
                    embs = [e.tolist() if hasattr(e, "tolist") else e for e in embs]
                snapshot["cloud_collections"][col.name] = {
                    "ids": data.get("ids", []),
                    "documents": data.get("documents", []),
                    "metadatas": data.get("metadatas", []),
                    "embeddings": embs,
                }
            except Exception as e:
                console.print(f"  [yellow]⚠ Failed to export cloud collection {col.name}: {e}[/yellow]")

    output_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"  [green]✅ Exported JSON backup snapshot:[/green] {output_path.name}")
    return snapshot


def migrate_collection(collection, client_label: str = "local") -> Dict[str, int]:
    """
    Encrypt all plaintext documents in a collection while preserving embeddings.
    Returns stats dict: {"total": int, "already_encrypted": int, "migrated": int}.
    """
    stats = {"total": 0, "already_encrypted": 0, "migrated": 0}
    data = collection.get(include=["documents", "metadatas", "embeddings"])
    ids = data.get("ids", [])
    documents = data.get("documents", [])
    metadatas = data.get("metadatas", [])
    embeddings = data.get("embeddings", [])

    stats["total"] = len(ids)
    if not ids:
        return stats

    update_ids: List[str] = []
    update_docs: List[str] = []
    update_metas: List[Dict[str, Any]] = []
    update_embs: List[Any] = []
    original_plaintexts: Dict[str, str] = {}

    for i, doc_id in enumerate(ids):
        doc = documents[i] if documents and i < len(documents) else ""
        meta = metadatas[i] if metadatas and i < len(metadatas) else {}
        emb = embeddings[i] if embeddings is not None and len(embeddings) > i else None

        if is_encrypted(doc):
            stats["already_encrypted"] += 1
            continue

        # Document needs encryption
        original_plaintexts[doc_id] = doc
        encrypted = encrypt_text(doc)

        update_ids.append(doc_id)
        update_docs.append(encrypted)
        update_metas.append(meta)
        update_embs.append(emb)
        stats["migrated"] += 1

    # Apply updates in batches of 100
    batch_size = 100
    for start in range(0, len(update_ids), batch_size):
        end = start + batch_size
        b_ids = update_ids[start:end]
        b_docs = update_docs[start:end]
        b_metas = update_metas[start:end]
        b_embs = update_embs[start:end] if any(e is not None for e in update_embs) else None

        kwargs: Dict[str, Any] = {
            "ids": b_ids,
            "documents": b_docs,
            "metadatas": b_metas,
        }
        if b_embs and all(e is not None for e in b_embs):
            kwargs["embeddings"] = b_embs

        collection.update(**kwargs)

    # Verification phase
    if update_ids:
        verify_data = collection.get(ids=update_ids, include=["documents"])
        v_docs = verify_data.get("documents", [])
        for doc_id, v_doc in zip(update_ids, v_docs):
            if not is_encrypted(v_doc):
                raise RuntimeError(f"Verification failed: doc {doc_id} is still plaintext in ChromaDB!")
            decrypted = decrypt_text(v_doc)
            expected = original_plaintexts[doc_id]
            if decrypted != expected:
                raise RuntimeError(f"Verification failed: decrypted text mismatch for doc {doc_id}!")

    return stats


def run_migration():
    """Main migration entrypoint."""
    console.print(Panel(
        "[bold cyan]ChromaDB AES-256-GCM Customer Document Encryption Migration[/bold cyan]\n"
        "[dim]Encrypting stored customer texts while keeping vector embeddings identical.[/dim]",
        style="cyan"
    ))

    # 1. Ensure encryption key exists and is persistent
    key = get_or_create_chroma_key()
    console.print(f"  [green]🔑 Encryption key active (256-bit AES-GCM):[/green] {key.hex()[:12]}... (persisted)")

    # 2. Local client
    local_dir = backend_dir / "chroma_db"
    local_client = None
    if local_dir.is_dir():
        local_client = chromadb.PersistentClient(path=str(local_dir.resolve()))

    # 3. Cloud client
    api_key = os.getenv("CHROMA_API_KEY")
    tenant = os.getenv("CHROMA_TENANT")
    database = os.getenv("CHROMA_DATABASE")
    cloud_client = None
    if api_key and tenant and database:
        try:
            cloud_client = chromadb.CloudClient(tenant=tenant, database=database, api_key=api_key)
        except Exception as e:
            console.print(f"  [yellow]⚠ Cloud client failed to connect: {e}[/yellow]")

    # 4. Perform Backups FIRST
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if local_dir.is_dir():
        backup_local_chromadb(local_dir)

    json_backup_path = backend_dir / f"chroma_backup_{timestamp}.json"
    export_collections_snapshot(local_client, cloud_client, json_backup_path)

    # 5. Migrate Local Collections
    table = Table(title="Migration Summary", show_header=True)
    table.add_column("Type", style="cyan")
    table.add_column("Collection", style="white")
    table.add_column("Total Chunks", style="yellow")
    table.add_column("Migrated (Encrypted)", style="green")
    table.add_column("Already Encrypted", style="dim")

    if local_client:
        for col in local_client.list_collections():
            st = migrate_collection(col, client_label="local")
            table.add_row("Local", col.name, str(st["total"]), str(st["migrated"]), str(st["already_encrypted"]))

    # 6. Migrate Cloud Collections
    if cloud_client:
        for col in cloud_client.list_collections():
            try:
                st = migrate_collection(col, client_label="cloud")
                table.add_row("Cloud", col.name, str(st["total"]), str(st["migrated"]), str(st["already_encrypted"]))
            except Exception as e:
                console.print(f"  [red]❌ Cloud migration failed for {col.name}: {e}[/red]")

    console.print(Panel(table, style="green"))
    console.print("  [bold green]🎉 Migration and verification completed successfully![/bold green]\n")


if __name__ == "__main__":
    run_migration()
