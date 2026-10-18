"""LLM prompts and strict JSON schemas (plan 16.6). Model calls go through services/llm.py."""

from typing import Any

TYPOLOGY_NAMES = [
    "structuring",
    "money_mule",
    "funnel",
    "layering",
    "round_trip",
    "pass_through",
    "scatter_gather",
]

TYPOLOGY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "typologies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": TYPOLOGY_NAMES},
                    "confidence": {"type": "number"},
                    "evidence_txn_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "confidence", "evidence_txn_ids"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["typologies", "summary"],
    "additionalProperties": False,
}

TYPOLOGY_SYSTEM = (
    "You label anti-money-laundering typologies from the evidence supplied. "
    "Use only the facts given. Choose from the allowed names only. "
    "Return a typology only when the transactions support it; otherwise return an empty list. "
    "Cite only transaction ids from the evidence. Return JSON only."
)

SAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "object",
            "properties": {"party_token": {"type": "string"}},
            "required": ["party_token"],
            "additionalProperties": False,
        },
        "activity": {
            "type": "object",
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
                "total_amount": {"type": "number"},
                "category": {"type": "string"},
            },
            "required": ["start", "end", "total_amount", "category"],
            "additionalProperties": False,
        },
        "narrative": {"type": "string"},
        "cited_txn_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["subject", "activity", "narrative", "cited_txn_ids"],
    "additionalProperties": False,
}

SAR_SYSTEM = (
    "You draft a US Suspicious Activity Report narrative for a human investigator. "
    "Use only the facts supplied. Refer to parties only by their tokens (PARTY_NNN). "
    "Cite at most 10 transaction ids, chosen from the evidence transactions, and only those "
    "that support the narrative. State facts, not conclusions of guilt. "
    "Return JSON only."
)
