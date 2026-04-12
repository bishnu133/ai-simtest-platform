"""Suite 5: Asset Registry HTTP API — all CRUD, versioning, lifecycle endpoints."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.assets.router import get_asset_service, get_tenant_context, router
from src.assets.service import AssetService
from src.common.models import ActorRef, TenantContext


@pytest.fixture
def app(local_adapter, ctx_tenant_a):
    """Build a FastAPI app with the assets router and deps overridden."""
    app = FastAPI()
    svc = AssetService(storage=local_adapter)

    async def override_ctx() -> TenantContext:
        return ctx_tenant_a

    async def override_svc() -> AssetService:
        return svc

    app.dependency_overrides[get_tenant_context] = override_ctx
    app.dependency_overrides[get_asset_service] = override_svc
    app.include_router(router)
    app.state.asset_service = svc  # fallback if override not applied
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestAssetRouter:
    def test_create_asset_returns_201(self, client):
        resp = client.post(
            "/v1/assets",
            json={
                "asset_type": "judge_pack",
                "name": "Standard 4 Judges",
                "slug": "standard-4-judges",
                "description": "Quality, safety, grounding, relevance",
                "content": {"judges": ["quality", "safety", "grounding", "relevance"]},
                "tags": {"tier": "default"},
                "changelog": "Initial version",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Standard 4 Judges"
        assert body["version"] == 1
        assert body["status"] == "draft"
        assert body["storage_tier"] == "inline"
        assert len(body["content_hash"]) == 64

    def test_create_duplicate_slug_returns_409(self, client):
        payload = {
            "asset_type": "policy_pack",
            "name": "Banking Policy",
            "slug": "banking-policy",
            "content": {"rules": []},
        }
        r1 = client.post("/v1/assets", json=payload)
        assert r1.status_code == 201
        r2 = client.post("/v1/assets", json=payload)
        assert r2.status_code == 409
        assert r2.json()["detail"]["error_code"] == "asset_slug_conflict"

    def test_get_asset_by_id(self, client):
        create = client.post(
            "/v1/assets",
            json={
                "asset_type": "scenario_pack",
                "name": "Adversarial Suite",
                "slug": "adversarial-suite",
                "content": {"scenarios": ["prompt_injection", "jailbreak"]},
            },
        )
        asset_id = create.json()["id"]

        resp = client.get(f"/v1/assets/{asset_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == asset_id

    def test_get_missing_asset_returns_404(self, client):
        resp = client.get("/v1/assets/nonexistent-id")
        assert resp.status_code == 404

    def test_list_assets_with_type_filter(self, client):
        client.post(
            "/v1/assets",
            json={"asset_type": "judge_pack", "name": "J1", "slug": "j1", "content": {}},
        )
        client.post(
            "/v1/assets",
            json={"asset_type": "policy_pack", "name": "P1", "slug": "p1", "content": {}},
        )

        resp = client.get("/v1/assets?asset_type=judge_pack")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["asset_type"] == "judge_pack" for i in items)

    def test_update_draft_changes_content_and_hash(self, client):
        create = client.post(
            "/v1/assets",
            json={
                "asset_type": "judge_pack",
                "name": "To Update",
                "slug": "to-update",
                "content": {"v": 1},
            },
        )
        asset_id = create.json()["id"]
        original_hash = create.json()["content_hash"]

        resp = client.patch(
            f"/v1/assets/{asset_id}",
            json={"content": {"v": 2}, "changelog": "Bumped"},
        )
        assert resp.status_code == 200
        assert resp.json()["content_hash"] != original_hash

    def test_cannot_update_approved_asset(self, client):
        create = client.post(
            "/v1/assets",
            json={"asset_type": "judge_pack", "name": "A", "slug": "a", "content": {"x": 1}},
        )
        asset_id = create.json()["id"]
        approve = client.post(f"/v1/assets/{asset_id}/approve", json={"changelog_note": ""})
        assert approve.status_code == 200
        assert approve.json()["status"] == "approved"

        resp = client.patch(f"/v1/assets/{asset_id}", json={"content": {"x": 2}})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "asset_immutable"

    def test_create_new_version_after_approval(self, client):
        create = client.post(
            "/v1/assets",
            json={"asset_type": "judge_pack", "name": "V", "slug": "vee", "content": {"x": 1}},
        )
        asset_id = create.json()["id"]
        client.post(f"/v1/assets/{asset_id}/approve", json={"changelog_note": ""})

        resp = client.post(
            f"/v1/assets/{asset_id}/versions",
            json={"content": {"x": 2}, "changelog": "Refinement of judge threshold"},
        )
        assert resp.status_code == 201
        assert resp.json()["version"] == 2
        assert resp.json()["status"] == "draft"
        assert resp.json()["parent_version"] == 1

    def test_list_versions_returns_all(self, client):
        create = client.post(
            "/v1/assets",
            json={"asset_type": "judge_pack", "name": "H", "slug": "hist", "content": {"x": 1}},
        )
        asset_id = create.json()["id"]
        client.post(f"/v1/assets/{asset_id}/approve", json={"changelog_note": ""})
        client.post(
            f"/v1/assets/{asset_id}/versions",
            json={"content": {"x": 2}, "changelog": "v2 update"},
        )

        resp = client.get(f"/v1/assets/{asset_id}/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["versions"]) == 2
        assert [v["version"] for v in body["versions"]] == [1, 2]

    def test_clone_asset_creates_new_asset_with_lineage(self, client):
        create = client.post(
            "/v1/assets",
            json={
                "asset_type": "judge_pack",
                "name": "Original",
                "slug": "original",
                "content": {"judges": ["a", "b"]},
            },
        )
        original_id = create.json()["id"]

        resp = client.post(
            f"/v1/assets/{original_id}/clone",
            json={"new_slug": "clone-of-original", "new_name": "Cloned Judges"},
        )
        assert resp.status_code == 201
        clone = resp.json()
        assert clone["id"] != original_id
        assert clone["cloned_from"] == original_id
        assert clone["slug"] == "clone-of-original"
