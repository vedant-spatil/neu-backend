"""yente client (plan 16.4).

Endpoint: POST {YENTE_URL}/match/{dataset}. Datasets: `sanctions`, `peps`.
Request/response envelope is taken from the running yente OpenAPI spec; see
`scripts/yente_openapi.py` (verify against the live container before relying on it).
"""

from typing import Any

import httpx

from app.config import get_settings

MATCH_THRESHOLD = 0.7
DATASETS = ("sanctions", "peps")


def match_party(name: str, dob: str | None, country: str | None) -> list[dict[str, Any]]:
    """Return candidate hits at or above MATCH_THRESHOLD across both datasets."""
    s = get_settings()
    props: dict[str, list[str]] = {"name": [name]}
    if dob:
        props["birthDate"] = [dob]
    if country:
        props["country"] = [country]
    body = {"queries": {"q1": {"schema": "Person", "properties": props}}}

    hits: list[dict[str, Any]] = []
    # yente runs on a small container with a 1 GB cap; a 5 s read timeout failed on the first slow search.
    with httpx.Client(timeout=30.0) as client:
        for ds in DATASETS:
            r = client.post(
                f"{s.yente_url}/match/{ds}",
                json=body,
                params={"threshold": MATCH_THRESHOLD},
            )
            r.raise_for_status()
            # Response: {"responses": {"q1": {"results": [{"id","caption","score","match",...}]}}}
            results = r.json().get("responses", {}).get("q1", {}).get("results", [])
            for item in results:
                if item.get("score", 0.0) >= MATCH_THRESHOLD:
                    hits.append(
                        {
                            "dataset": ds,
                            "id": item.get("id"),
                            "caption": item.get("caption"),
                            "score": item.get("score"),
                            "program_id": (item.get("properties", {}) or {}).get("programId"),
                        }
                    )
    return hits
