"""A tiny fake shop the agent operates on: customers, orders, an outbox.

Each run gets its OWN copy of the seed data (nothing persists, runs can't
interfere), so you can approve refunds freely and start over.

Tools are tagged by RISK — the core idea of human-in-the-loop design:
  read     lookups — run automatically
  write    internal, reversible (notes) — run automatically
  money    refunds — auto-approved up to HITL_AUTO_APPROVE_LIMIT, else a human decides
  external emails to customers — always a human (can't be unsent)
"""

import copy
import json
import os
from typing import Any

AUTO_APPROVE_LIMIT = float(os.getenv("HITL_AUTO_APPROVE_LIMIT", "50"))

SEED = {
    "customers": {
        "jane@example.com": {"name": "Jane Doe", "tier": "gold"},
        "raj@example.com": {"name": "Raj Patel", "tier": "standard"},
        "li@example.com": {"name": "Li Wei", "tier": "standard"},
    },
    "orders": {
        "A1001": {"customer": "jane@example.com", "item": "Wireless headphones", "total": 89.0, "status": "delivered", "refunded": 0.0, "notes": []},
        "A1002": {"customer": "jane@example.com", "item": "Standing desk", "total": 420.0, "status": "delivered", "refunded": 0.0, "notes": []},
        "A1003": {"customer": "raj@example.com", "item": "USB-C hub", "total": 35.0, "status": "shipped", "refunded": 0.0, "notes": []},
        "A1004": {"customer": "li@example.com", "item": "Espresso machine", "total": 260.0, "status": "delivered", "refunded": 0.0, "notes": []},
    },
    "outbox": [],
}

POLICY = """\
- Verify the order belongs to the customer making the request before acting.
- Refunds only for DELIVERED orders, at most the order total minus what was already refunded.
- After a refund, email the customer to confirm it.
- If you can't help, explain why and add a note to the order."""


def _fn(name: str, description: str, props: dict[str, Any]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": list(props)}}}


TOOLS = [
    _fn("lookup_customer", "Find a customer by email.", {"email": {"type": "string"}}),
    _fn("lookup_order", "Get an order by id (e.g. A1002).", {"order_id": {"type": "string"}}),
    _fn("add_note", "Add an internal note to an order.", {"order_id": {"type": "string"}, "note": {"type": "string"}}),
    _fn("issue_refund", "Refund (part of) an order to the customer's card.",
        {"order_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}}),
    _fn("send_email", "Send an email to a customer.",
        {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}),
]

RISK = {"lookup_customer": "read", "lookup_order": "read", "add_note": "write",
        "issue_refund": "money", "send_email": "external"}


def needs_approval(tool: str, args: dict[str, Any]) -> str | None:
    """The approval POLICY, in code: None = run automatically, else the reason
    a human must decide."""
    risk = RISK.get(tool)
    if risk == "external":
        return "Sends a message outside the company — can't be undone."
    if risk == "money":
        try:
            amount = float(args.get("amount", 0))
        except (TypeError, ValueError):
            return "Refund amount is not a number."
        if amount > AUTO_APPROVE_LIMIT:
            return f"Refund of ${amount:.2f} is above the ${AUTO_APPROVE_LIMIT:.0f} auto-approve limit."
    return None


class World:
    def __init__(self) -> None:
        self.data = copy.deepcopy(SEED)

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    def execute(self, tool: str, args: dict[str, Any]) -> str:
        """Run a tool; errors come back as text the agent can react to."""
        orders, customers = self.data["orders"], self.data["customers"]
        try:
            if tool == "lookup_customer":
                email = str(args["email"]).strip().lower()
                c = customers.get(email)
                if not c:
                    return f"No customer with email {email}."
                mine = {k: v["item"] for k, v in orders.items() if v["customer"] == email}
                return json.dumps({"email": email, **c, "orders": mine})
            if tool == "lookup_order":
                oid = str(args["order_id"]).strip().upper()
                return json.dumps({"order_id": oid, **orders[oid]}) if oid in orders else f"No order {oid}."
            if tool == "add_note":
                oid = str(args["order_id"]).strip().upper()
                if oid not in orders:
                    return f"No order {oid}."
                orders[oid]["notes"].append(str(args["note"])[:300])
                return f"Note added to {oid}."
            if tool == "issue_refund":
                oid = str(args["order_id"]).strip().upper()
                amount = round(float(args["amount"]), 2)
                o = orders.get(oid)
                if not o:
                    return f"Refund failed: no order {oid}."
                if o["status"] != "delivered":
                    return f"Refund failed: order {oid} is '{o['status']}', not delivered."
                if amount <= 0 or amount > o["total"] - o["refunded"] + 1e-9:
                    return f"Refund failed: amount must be between 0 and {o['total'] - o['refunded']:.2f}."
                o["refunded"] = round(o["refunded"] + amount, 2)
                if o["refunded"] >= o["total"]:
                    o["status"] = "refunded"
                return f"Refunded ${amount:.2f} on {oid}. Total refunded: ${o['refunded']:.2f}."
            if tool == "send_email":
                self.data["outbox"].append({k: str(args[k])[:2000] for k in ("to", "subject", "body")})
                return f"Email sent to {args['to']}."
            return f"Unknown tool {tool!r}."
        except (KeyError, TypeError, ValueError) as exc:
            return f"Tool error: bad or missing argument ({exc})."
