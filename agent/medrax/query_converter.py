"""Qwen query conversion for the RAG's existing translate adapter slot."""

import json
import re
from medrax.answer_audit import QwenClaimTranslator


class QwenQueryConverter:
    def __init__(self):
        self.client = QwenClaimTranslator()
        self._cache = {}

    def convert(self, question):
        question = str(question).strip()
        if not re.search(r"[\u3400-\u9fff]", question):
            return question
        if question in self._cache:
            return self._cache[question]
        response = self.client._get_model().invoke([
            ("system", "Convert the original Chinese medical question into a concise English "
             "biomedical search query. Preserve diseases, drugs, population, relevant numbers, "
             "units, negation, comparisons and time constraints. Remove interface/workflow "
             "instructions such as reading HIS or opening citations. Never infer patient "
             "facts, add a diagnosis, include patient identifiers, or produce an answer. "
             "Return groups of English clinical concepts: all groups must be present (AND), "
             "terms within one group are true synonyms (OR). The first group must be the "
             "main disease/drug/topic; do not put study types in a separate OR route. Omit "
             "generic words such as evidence, research or article; add trial, guideline or "
             "review filters only when explicitly requested. Do not write Boolean operators "
             "or PubMed field tags inside terms. This is a machine-generated candidate, "
             'not reviewed terminology. Return JSON only, e.g. {"concepts":[["asthma"],["treatment","therapy"]]}.'),
            ("human", question),
        ])
        text = self.client._strip_json_fence(self.client._message_text(response.content))
        value = json.loads(text)
        concepts = value.get("concepts") if isinstance(value, dict) else None
        if not isinstance(concepts, list) or not 1 <= len(concepts) <= 8:
            raise ValueError("Query conversion requires 1 to 8 clinical concept groups")
        groups = []
        for terms in concepts:
            if not isinstance(terms, list) or not 1 <= len(terms) <= 5:
                raise ValueError("Each clinical concept needs 1 to 5 synonymous terms")
            cleaned = []
            for term in terms:
                if not isinstance(term, str) or not term.strip() or len(term) > 120:
                    raise ValueError("Invalid English clinical concept")
                term = term.strip()
                if re.search(r'[\u3400-\u9fff\[\]"\r\n]|\b(?:AND|OR|NOT)\b', term):
                    raise ValueError("Clinical terms must not contain Boolean operators or field tags")
                cleaned.append(term)
            groups.append("(" + " OR ".join(f'"{term}"' for term in dict.fromkeys(cleaned)) + ")")
        query = " AND ".join(groups)
        if len(self._cache) >= 256:
            self._cache.clear()
        self._cache[question] = query
        return query
