"""Tests for the ``/api/targets`` REST endpoints."""

from unittest.mock import patch


class TestListTargets:
    """GET /api/targets."""

    def test_empty_list(self, client):
        """Returns an empty list when no targets exist."""
        resp = client.get("/api/targets")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCreateTarget:
    """POST /api/targets."""

    @patch("pingwatcher.api.targets.start_monitoring")
    def test_create_target(self, mock_start, client):
        """Creating a target returns 201 and starts monitoring."""
        resp = client.post(
            "/api/targets",
            json={"host": "8.8.8.8", "label": "Google DNS"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["host"] == "8.8.8.8"
        assert data["label"] == "Google DNS"
        assert data["active"] is True
        mock_start.assert_called_once()

    @patch("pingwatcher.api.targets.start_monitoring")
    def test_create_with_custom_interval(self, mock_start, client):
        """Custom trace_interval is respected."""
        resp = client.post(
            "/api/targets",
            json={"host": "1.1.1.1", "trace_interval": 10.0},
        )
        assert resp.status_code == 201
        assert resp.json()["trace_interval"] == 10.0

    def test_create_missing_host(self, client):
        """Missing host field returns 422."""
        resp = client.post("/api/targets", json={})
        assert resp.status_code == 422


class TestGetTarget:
    """GET /api/targets/{id}."""

    @patch("pingwatcher.api.targets.start_monitoring")
    def test_get_existing(self, mock_start, client):
        """Fetching an existing target returns 200."""
        create = client.post("/api/targets", json={"host": "1.2.3.4"})
        tid = create.json()["id"]

        resp = client.get(f"/api/targets/{tid}")
        assert resp.status_code == 200
        assert resp.json()["host"] == "1.2.3.4"

    def test_get_missing(self, client):
        """Fetching a non-existent target returns 404."""
        resp = client.get("/api/targets/does-not-exist")
        assert resp.status_code == 404


class TestDeleteTarget:
    """DELETE /api/targets/{id}."""

    @patch("pingwatcher.api.targets.stop_monitoring")
    @patch("pingwatcher.api.targets.start_monitoring")
    def test_delete_existing(self, mock_start, mock_stop, client):
        """Deleting a target returns 204 and stops monitoring."""
        create = client.post("/api/targets", json={"host": "9.9.9.9"})
        tid = create.json()["id"]

        resp = client.delete(f"/api/targets/{tid}")
        assert resp.status_code == 204
        mock_stop.assert_called_once_with(tid)

    @patch("pingwatcher.api.targets.stop_monitoring")
    def test_delete_missing(self, mock_stop, client):
        """Deleting a non-existent target returns 404."""
        resp = client.delete("/api/targets/nope")
        assert resp.status_code == 404
