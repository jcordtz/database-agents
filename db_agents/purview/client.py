"""Client for looking up table/column governance information in Microsoft
Purview's Data Map (Atlas) API by qualified name.

Auth uses an Azure AD service principal (client credentials flow) via
azure-identity's ClientSecretCredential. Network/lookup failures are, by
default, swallowed (fail_silently=True) so that a slow or unreachable
Purview account never blocks table-agent creation -- Purview data is treated
as an optional enrichment on top of the database-native metadata.
"""
from __future__ import annotations

import logging

import httpx
from azure.identity import ClientSecretCredential

from db_agents.config import PurviewConfig
from db_agents.purview.models import PurviewAssetInfo, PurviewColumnInfo, PurviewContact, PurviewGlossaryTerm

logger = logging.getLogger(__name__)

_ATLAS_SCOPE = "https://purview.azure.net/.default"


class PurviewClient:
    def __init__(self, config: PurviewConfig):
        if not config.account_endpoint:
            raise ValueError("Purview config is enabled but 'account_endpoint' is not set")
        self._config = config
        self._credential = ClientSecretCredential(
            tenant_id=config.tenant_id,
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
        self._http = httpx.Client(
            base_url=config.account_endpoint.rstrip("/"),
            timeout=config.request_timeout_seconds,
        )

    def _access_token(self) -> str:
        token = self._credential.get_token(_ATLAS_SCOPE)
        return token.token

    def _get(self, path: str, params: dict) -> dict | None:
        try:
            token = self._access_token()
            response = self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.warning("Purview lookup failed for %s (params=%s)", path, params, exc_info=True)
            if self._config.fail_silently:
                return None
            raise

    def lookup_table(self, qualified_name: str, entity_type: str) -> PurviewAssetInfo | None:
        """Look up a table/view entity by its unique qualifiedName attribute."""
        path = "/datamap/api/atlas/v2/entity/uniqueAttribute/type/" + entity_type
        data = self._get(path, params={"attr:qualifiedName": qualified_name})
        if data is None:
            return None
        return self._parse_entity(data)

    def lookup_column(self, qualified_name: str, entity_type: str) -> PurviewColumnInfo | None:
        """Look up a column entity by its unique qualifiedName attribute."""
        path = "/datamap/api/atlas/v2/entity/uniqueAttribute/type/" + entity_type
        data = self._get(path, params={"attr:qualifiedName": qualified_name})
        if data is None:
            return None
        entity = data.get("entity", {})
        attrs = entity.get("attributes", {})
        return PurviewColumnInfo(
            qualified_name=qualified_name,
            guid=entity.get("guid"),
            description=attrs.get("userDescription") or attrs.get("description"),
            classifications=[c.get("typeName", "") for c in entity.get("classifications", []) or []],
            glossary_terms=[
                PurviewGlossaryTerm(name=t.get("displayText", ""), guid=t.get("guid"))
                for t in entity.get("meanings", []) or []
            ],
        )

    def _parse_entity(self, data: dict) -> PurviewAssetInfo:
        entity = data.get("entity", {})
        attrs = entity.get("attributes", {})
        qualified_name = attrs.get("qualifiedName", "")
        return PurviewAssetInfo(
            qualified_name=qualified_name,
            guid=entity.get("guid"),
            entity_type=entity.get("typeName"),
            description=attrs.get("userDescription") or attrs.get("description"),
            classifications=[c.get("typeName", "") for c in entity.get("classifications", []) or []],
            glossary_terms=[
                PurviewGlossaryTerm(name=t.get("displayText", ""), guid=t.get("guid"))
                for t in entity.get("meanings", []) or []
            ],
            contacts=[
                PurviewContact(role=role, identifier=c.get("id", ""))
                for role, contacts in (entity.get("contacts", {}) or {}).items()
                for c in contacts
            ],
            source_url=(
                f"{self._config.account_endpoint.rstrip('/')}/catalog/asset/{entity.get('guid')}"
                if entity.get("guid")
                else None
            ),
        )

    def close(self) -> None:
        self._http.close()
