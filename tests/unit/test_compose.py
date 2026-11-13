from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"


def test_compose_does_not_publish_db_port() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    assert {"app", "db"} <= set(services)
    assert services["db"]["image"].startswith("pgvector/pgvector:pg18")
    assert "ports" not in services["db"]
