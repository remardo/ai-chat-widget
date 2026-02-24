#!/usr/bin/env python3
"""Sync Supabase business data into a markdown knowledge file for RAG."""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


def getenv(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def normalize_rest_url(url: str) -> str:
    u = (url or "").rstrip("/")
    if u.endswith("/rest/v1"):
        return u
    return u + "/rest/v1"


def apply_prefix(table: str, prefix: str) -> str:
    name = (table or "").strip()
    pref = (prefix or "").strip()
    if not name:
        return ""
    if pref and not name.startswith(pref):
        return f"{pref}{name}"
    return name


def fetch_table(
    client: httpx.Client,
    rest_base: str,
    headers: Dict[str, str],
    table: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if not table:
        return []
    url = f"{rest_base}/{table}"
    params = {"select": "*", "limit": str(limit)}
    response = client.get(url, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return data
    return []


def row_to_line(row: Dict[str, Any], preferred: List[str], max_fields: int = 8) -> str:
    result: List[str] = []
    used = set()

    for key in preferred:
        if key in row and row[key] not in (None, "", []):
            result.append(f"{key}: {row[key]}")
            used.add(key)
        if len(result) >= max_fields:
            return "; ".join(result)

    for key, value in row.items():
        if key in used or value in (None, "", []):
            continue
        result.append(f"{key}: {value}")
        if len(result) >= max_fields:
            break
    return "; ".join(result)


def build_markdown(
    doors: List[Dict[str, Any]],
    promos: List[Dict[str, Any]],
    company: List[Dict[str, Any]],
    *,
    source: str,
    table_doors: str,
    table_promos: str,
    table_company: str,
) -> str:
    now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: List[str] = [
        "# Live Data From Supabase",
        "",
        f"- Updated: {now}",
        f"- Source: {source}",
        f"- Tables: doors=`{table_doors}`, promotions=`{table_promos}`, company=`{table_company}`",
        "",
        "Use this block as fresh commercial knowledge: current products, prices, promotions, addresses, and contacts.",
        "",
        "## Doors Catalog",
    ]

    if doors:
        for row in doors:
            lines.append(
                "- "
                + row_to_line(
                    row,
                    ["name", "model", "series", "price", "currency", "in_stock", "material", "color", "glass"],
                )
            )
    else:
        lines.append("- No rows fetched.")

    lines.append("")
    lines.append("## Promotions")
    if promos:
        for row in promos:
            lines.append(
                "- "
                + row_to_line(
                    row,
                    ["title", "name", "description", "discount", "valid_from", "valid_to", "is_active"],
                )
            )
    else:
        lines.append("- No rows fetched.")

    lines.append("")
    lines.append("## Company Info")
    if company:
        for row in company:
            lines.append(
                "- "
                + row_to_line(
                    row,
                    ["name", "showroom_address", "phone", "email", "working_hours", "delivery", "installation"],
                )
            )
    else:
        lines.append("- No rows fetched.")

    lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def trigger_reload(reload_url: str, timeout: int) -> Tuple[bool, str]:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(reload_url)
        if 200 <= resp.status_code < 300:
            return True, resp.text
        return False, f"HTTP {resp.status_code}: {resp.text[:500]}"
    except Exception as e:
        return False, str(e)


def main() -> int:
    if load_dotenv is not None:
        # Load backend/.env when running from repo.
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    parser = argparse.ArgumentParser(description="Sync Supabase data into knowledge markdown.")
    parser.add_argument("--output", default=getenv("SUPABASE_SYNC_OUTPUT", "knowledge/supabase-live-rag.md"))
    parser.add_argument("--limit-doors", type=int, default=int(getenv("SUPABASE_SYNC_LIMIT_DOORS", "200")))
    parser.add_argument("--limit-promos", type=int, default=int(getenv("SUPABASE_SYNC_LIMIT_PROMOS", "100")))
    parser.add_argument("--limit-company", type=int, default=int(getenv("SUPABASE_SYNC_LIMIT_COMPANY", "50")))
    parser.add_argument("--timeout", type=int, default=int(getenv("SUPABASE_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--reload-url", default=getenv("SUPABASE_SYNC_RELOAD_URL", ""))
    args = parser.parse_args()

    supabase_url = getenv("SUPABASE_URL")
    supabase_key = getenv("SUPABASE_SERVICE_ROLE_KEY")
    table_prefix = getenv("SUPABASE_TABLE_PREFIX", "aftora_")
    table_doors = apply_prefix(getenv("SUPABASE_TABLE_DOORS", "aftora_doors"), table_prefix)
    table_promos = apply_prefix(getenv("SUPABASE_TABLE_PROMOTIONS", ""), table_prefix)
    table_company = apply_prefix(getenv("SUPABASE_TABLE_COMPANY", ""), table_prefix)

    if not supabase_url or not supabase_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

    rest_base = normalize_rest_url(supabase_url)
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=args.timeout) as client:
        doors = fetch_table(client, rest_base, headers, table_doors, args.limit_doors)
        promos = fetch_table(client, rest_base, headers, table_promos, args.limit_promos)
        company = fetch_table(client, rest_base, headers, table_company, args.limit_company)

    content = build_markdown(
        doors,
        promos,
        company,
        source=rest_base,
        table_doors=table_doors,
        table_promos=table_promos,
        table_company=table_company,
    )

    output_path = Path(args.output)
    if not output_path.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        output_path = project_root / output_path

    atomic_write(output_path, content)
    print(f"OK: synced to {output_path}")
    print(f"Rows: doors={len(doors)} promos={len(promos)} company={len(company)}")

    if args.reload_url:
        ok, details = trigger_reload(args.reload_url, args.timeout)
        if ok:
            print(f"OK: knowledge reload triggered -> {args.reload_url}")
        else:
            print(f"WARN: reload failed -> {details}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
