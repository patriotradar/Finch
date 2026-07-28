"""Legacy file-backed CRM compatibility layer for Aegis deal handoffs."""

import json, os
from datetime import datetime


class CRM:
    def __init__(self, data_dir="./data/clients/"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.deals_file = os.path.join(data_dir, "deals.json")
        self.clients_file = os.path.join(data_dir, "clients.json")
        self._load()

    def _load(self):
        self.deals = json.load(open(self.deals_file)) if os.path.exists(self.deals_file) else {}
        self.clients = json.load(open(self.clients_file)) if os.path.exists(self.clients_file) else {}

    def _save_deals(self):
        json.dump(self.deals, open(self.deals_file, "w"), indent=2, default=str)

    def _save_clients(self):
        json.dump(self.clients, open(self.clients_file, "w"), indent=2, default=str)

    def add_deal(self, company, contact_email, pricing_result, source="outbound"):
        deal_id = company.lower().replace(" ", "_")
        self.deals[deal_id] = {
            "id": deal_id,
            "company": company,
            "contact_email": contact_email,
            "price": pricing_result,
            "source": source,
            "stage": "lead",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "history": [{"stage": "lead", "at": datetime.now().isoformat()}],
        }
        self._save_deals()
        return deal_id

    def update_stage(self, deal_id, new_stage, note=""):
        if deal_id not in self.deals:
            return False
        self.deals[deal_id]["stage"] = new_stage
        self.deals[deal_id]["updated_at"] = datetime.now().isoformat()
        self.deals[deal_id]["history"].append({"stage": new_stage, "at": datetime.now().isoformat(), "note": note})
        if new_stage == "won":
            self.clients[deal_id] = {
                **self.deals[deal_id],
                "active_since": datetime.now().isoformat(),
                "status": "active",
            }
            self._save_clients()
        self._save_deals()
        return True

    def pipeline_summary(self):
        stages = {}
        for deal in self.deals.values():
            stage = deal["stage"]
            stages[stage] = stages.get(stage, 0) + 1
        annual_revenue = sum(
            c.get("price", {}).get("annual_price", c.get("price", {}).get("annual", 0))
            for c in self.clients.values()
            if c.get("status") == "active"
        )
        return {
            "stages": stages,
            "total_deals": len(self.deals),
            "active_clients": len(self.clients),
            "annual_revenue": annual_revenue,
            "mrr": 0,
        }

    def get_active_clients(self):
        return [c for c in self.clients.values() if c.get("status") == "active"]
