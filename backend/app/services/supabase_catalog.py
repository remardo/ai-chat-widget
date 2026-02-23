"""Live catalog/promo/company context from Supabase for chat responses."""

from __future__ import annotations

import logging
import re
import time
import asyncio
from typing import Any, Dict, List, Tuple

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class SupabaseCatalogService:
    """Fetch and format live business data from Supabase REST API."""

    def __init__(self) -> None:
        self.url = (settings.SUPABASE_URL or "").rstrip("/")
        self.key = settings.SUPABASE_SERVICE_ROLE_KEY or ""
        self.table_doors = settings.SUPABASE_TABLE_DOORS
        self.table_promotions = settings.SUPABASE_TABLE_PROMOTIONS
        self.table_company = settings.SUPABASE_TABLE_COMPANY
        self.enabled = bool(self.url and self.key)
        self.max_items = max(1, settings.SUPABASE_CONTEXT_MAX_ITEMS)
        self.cache_ttl = max(10, settings.SUPABASE_CACHE_TTL_SECONDS)
        self.timeout = max(5, settings.SUPABASE_TIMEOUT_SECONDS)
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

    @property
    def rest_base(self) -> str:
        if self.url.endswith("/rest/v1"):
            return self.url
        return self.url + "/rest/v1"

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    async def _fetch_table(self, table: str, limit: int) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []

        now = time.time()
        cached = self._cache.get(table)
        if cached and cached[0] > now:
            return cached[1]

        url = f"{self.rest_base}/{table}"
        params = {"select": "*", "limit": str(limit)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                logger.warning("Supabase table %s returned non-list payload", table)
                return []
            self._cache[table] = (now + self.cache_ttl, rows)
            return rows
        except Exception as e:
            logger.warning("Supabase table fetch failed (%s): %s", table, e)
            return []

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-Zа-яА-Я0-9_-]{2,}", (text or "").lower()) if len(t) > 1]

    @staticmethod
    def _row_blob(row: Dict[str, Any]) -> str:
        return " ".join(str(v) for v in row.values() if v is not None).lower()

    def _pick(self, rows: List[Dict[str, Any]], tokens: List[str], top_k: int) -> List[Dict[str, Any]]:
        if not rows:
            return []
        if not tokens:
            return rows[:top_k]

        scored: List[Tuple[int, Dict[str, Any]]] = []
        for row in rows:
            blob = self._row_blob(row)
            score = sum(1 for t in tokens if t in blob)
            if score > 0:
                scored.append((score, row))

        if not scored:
            return rows[:top_k]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:top_k]]

    @staticmethod
    def _row_to_line(row: Dict[str, Any], preferred_keys: List[str]) -> str:
        pairs: List[str] = []
        used = set()

        for key in preferred_keys:
            if key in row and row[key] not in (None, "", []):
                pairs.append(f"{key}: {row[key]}")
                used.add(key)
            if len(pairs) >= 6:
                return "; ".join(pairs)

        for key, value in row.items():
            if key in used or value in (None, "", []):
                continue
            pairs.append(f"{key}: {value}")
            if len(pairs) >= 6:
                break
        return "; ".join(pairs)

    async def get_live_context(self, query: str) -> str:
        """Return a concise context block from live Supabase data."""
        if not self.enabled:
            return ""

        tokens = self._tokenize(query)

        doors, promos, company = await self._fetch_all()
        selected_doors = self._pick(doors, tokens, self.max_items)
        selected_promos = self._pick(promos, tokens, self.max_items)
        selected_company = self._pick(company, tokens, max(1, min(self.max_items, 3)))

        sections: List[str] = []
        if selected_doors:
            lines = [
                "- " + self._row_to_line(
                    row,
                    ["name", "model", "series", "price", "currency", "in_stock", "material", "color", "glass"],
                )
                for row in selected_doors
            ]
            sections.append("АКТУАЛЬНЫЙ КАТАЛОГ ДВЕРЕЙ:\n" + "\n".join(lines))

        if selected_promos:
            lines = [
                "- " + self._row_to_line(
                    row,
                    ["title", "name", "description", "discount", "valid_from", "valid_to", "is_active"],
                )
                for row in selected_promos
            ]
            sections.append("ТЕКУЩИЕ АКЦИИ:\n" + "\n".join(lines))

        if selected_company:
            lines = [
                "- " + self._row_to_line(
                    row,
                    ["name", "showroom_address", "phone", "email", "working_hours", "delivery", "installation"],
                )
                for row in selected_company
            ]
            sections.append("ИНФОРМАЦИЯ О КОМПАНИИ:\n" + "\n".join(lines))

        return "\n\n".join(sections)

    async def _fetch_all(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        return await asyncio.gather(
            self._fetch_table(self.table_doors, max(20, self.max_items * 10)),
            self._fetch_table(self.table_promotions, max(10, self.max_items * 5)),
            self._fetch_table(self.table_company, 20),
        )

    async def check_connection(self) -> Dict[str, Any]:
        """Diagnostic status for API endpoint."""
        if not self.enabled:
            return {"enabled": False, "reason": "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing"}

        rows = await self._fetch_table(self.table_company, 1)
        return {
            "enabled": True,
            "rest_base": self.rest_base,
            "table_company": self.table_company,
            "company_rows_probe": len(rows),
        }


supabase_catalog_service = SupabaseCatalogService()
