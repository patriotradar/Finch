"""
Document handling via Telegram — forward contracts, invoices, onboarding docs
to Finch from your phone. He stores them, logs them to CRM, and can email them
to prospects when you say "/send contract to acmecorp@email.com".
"""

import os
import json
from datetime import datetime
from pathlib import Path


class FinchDocs:
    """Handles documents forwarded to Finch via Telegram."""

    def __init__(self, docs_dir="./data/documents/", crm=None):
        self.docs_dir = Path(docs_dir)
        try:
            self.docs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.docs_dir = Path("/tmp/finch-docs")
            self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.crm = crm
        self.registry = self._load_registry()

    def _load_registry(self):
        path = self.docs_dir / "registry.json"
        if path.exists():
            return json.load(open(path))
        return {"documents": []}

    def _save_registry(self):
        json.dump(self.registry, open(self.docs_dir / "registry.json", "w"), indent=2, default=str)

    def receive_document(self, file_path, file_name, sender="unknown", tags=None):
        """Receive a forwarded document, save it, and log it."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{timestamp}_{file_name}"
        dest = self.docs_dir / safe_name

        # Copy the file
        import shutil
        shutil.copy(file_path, dest)

        entry = {
            "id": len(self.registry["documents"]) + 1,
            "original_name": file_name,
            "stored_as": safe_name,
            "received_at": datetime.now().isoformat(),
            "from": sender,
            "tags": tags or [],
            "path": str(dest),
        }
        self.registry["documents"].append(entry)
        self._save_registry()

        return entry

    def list_documents(self, tag=None, limit=20):
        """List stored documents, optionally filtered by tag."""
        docs = self.registry["documents"]
        if tag:
            docs = [d for d in docs if tag in d.get("tags", [])]
        return docs[-limit:]

    def tag_document(self, doc_id, tag):
        """Tag a document for organization (e.g., 'contract', 'invoice', 'onboarding')."""
        for doc in self.registry["documents"]:
            if doc["id"] == doc_id:
                if tag not in doc.get("tags", []):
                    doc.setdefault("tags", []).append(tag)
                    self._save_registry()
                return doc
        return None

    def link_to_deal(self, doc_id, client_name):
        """Link a document to a specific client/deal in CRM."""
        for doc in self.registry["documents"]:
            if doc["id"] == doc_id:
                doc["linked_client"] = client_name
                doc.setdefault("tags", []).append("linked_to_deal")
                self._save_registry()
                return doc
        return None

    def get_documents_for_client(self, client_name):
        """Get all documents linked to a specific client."""
        return [d for d in self.registry["documents"]
                if d.get("linked_client") == client_name]

    def build_summary(self):
        """Human-readable summary of stored documents."""
        docs = self.registry["documents"]
        if not docs:
            return "No documents stored yet."

        contract_count = sum(1 for d in docs if "contract" in d.get("tags", []))
        onboarding_count = sum(1 for d in docs if "onboarding" in d.get("tags", []))
        invoice_count = sum(1 for d in docs if "invoice" in d.get("tags", []))

        summary = (
            f"📁 {len(docs)} documents stored\n"
            f"   Contracts: {contract_count}\n"
            f"   Onboarding: {onboarding_count}\n"
            f"   Invoices: {invoice_count}\n\n"
            f"Recent:"
        )
        for doc in docs[-5:]:
            summary += f"\n  #{doc['id']}: {doc['original_name']} ({', '.join(doc.get('tags', ['untagged']))})"
        return summary
