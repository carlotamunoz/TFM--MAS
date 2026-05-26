from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Dict, Any, List
import requests


@dataclass
class SparqlResponse:
    query: str
    raw: Dict[str, Any]
    bindings: List[Dict[str, Any]]
    vars: List[str]


class SparqlExecutor:
    def __init__(self, endpoint: str = "http://localhost:3030/dron/query", timeout: int = 30):
        self.endpoint = endpoint
        self.timeout = timeout

    def run_raw(self, sparql: str, accept: str = "application/sparql-results+json") -> requests.Response:
        return requests.post(
            self.endpoint,
            data={"query": sparql},
            headers={"Accept": accept},
            timeout=self.timeout,
        )

    def run(self, sparql: str) -> SparqlResponse:
        resp = self.run_raw(sparql, accept="application/sparql-results+json")
        resp.raise_for_status()
        raw = resp.json()
        vars_ = raw.get("head", {}).get("vars", [])
        bindings = raw.get("results", {}).get("bindings", [])
        return SparqlResponse(query=sparql, raw=raw, bindings=bindings, vars=vars_)

    def run_any(self, sparql: str):
        # Detecta CONSTRUCT vs SELECT
        if re.search(r"\\bconstruct\\b", sparql, flags=re.I):
            resp = requests.post(
                self.endpoint,
                data={"query": sparql},
                headers={"Accept": "text/turtle"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return {"type": "construct", "turtle": resp.text}
        else:
            res = self.run(sparql)  # tu run actual (SELECT -> JSON)
            return {"type": "select", "vars": res.vars, "bindings": res.bindings}

    @staticmethod
    def simplify_bindings(bindings: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        return [{k: v.get("value") for k, v in row.items()} for row in bindings]
