import json
import shutil
from datetime import date
from importlib.resources import files
from uuid import uuid4

import pytest

from app.common.errors import AppError
from app.common.models import DataQuality, EntryMode, Source
from app.rules.registry import COUNTRIES, FileRuleRegistry

CHECKED_ON = date(2026, 10, 25)
EXPIRED_ON = date(2026, 11, 2)


def test_public_catalog_and_scenario_contract(client):
    response = client.get("/api/v1/destinations")
    assert response.status_code == 200
    destinations = response.json()["items"]
    assert len(destinations) == 12
    assert {item["country"] for item in destinations} == COUNTRIES
    for destination in destinations:
        response = client.get("/api/v1/scenarios/" + destination["scenario_id"])
        assert response.status_code == 200
        scenario = response.json()
        assert scenario["version"] == destination["version"]
        assert scenario["requirements"] and scenario["process_stages"] and scenario["sources"]
        assert scenario["coverage"] == "partial" and scenario["unverified_conditions"]
    response = client.get(f"/api/v1/scenarios/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scenario_not_found"


def test_distinct_modes_and_country_specific_requirements():
    registry = FileRuleRegistry()
    assert registry.get_scenario("IN_TOURISM", as_of=CHECKED_ON).entry_mode == EntryMode.EVISA
    assert registry.get_scenario("EG_TOURISM", as_of=CHECKED_ON).entry_mode == EntryMode.EVISA
    for country in ("ZA", "BR", "AR"):
        scenario = registry.get_scenario(country + "_TOURISM", as_of=CHECKED_ON)
        assert scenario.entry_mode == EntryMode.VISA_FREE
        assert {event for stage in scenario.process_stages for event in stage.event_types} == {"entry_preparation_completed"}
    for country in ("FR", "ES", "JP", "US", "CA", "AU", "NZ"):
        assert registry.get_scenario(country + "_TOURISM", as_of=CHECKED_ON).entry_mode == EntryMode.VISA
    for country in ("BR", "ZA"):
        types = {req.document_type for req in registry.get_scenario(country + "_TOURISM", as_of=CHECKED_ON).requirements}
        assert "insurance" not in types and "bank_statement" not in types
    nz = registry.get_scenario("NZ_TOURISM", as_of=CHECKED_ON)
    assert not next(req for req in nz.requirements if req.document_type == "insurance").mandatory
    jp = registry.get_scenario("JP_TOURISM", as_of=CHECKED_ON)
    assert next(req for req in jp.requirements if req.document_type == "bank_statement").mandatory


def test_versions_source_quality_and_defensive_copies():
    registry = FileRuleRegistry()
    ruleset = registry.get_ruleset(registry.active_version, as_of=CHECKED_ON)
    assert len({rule.scenario_id for rule in ruleset.rules}) == 12
    assert len(ruleset.rules) == len({rule.id for rule in ruleset.rules})
    fr = registry.get_scenario("FR_TOURISM", as_of=CHECKED_ON)
    assert fr.entry_mode == "visa" and fr.data_quality == DataQuality.NEEDS_VERIFICATION
    assert all(source.checked_at and source.evidence for source in fr.sources)
    fr.requirements.clear()
    assert registry.get_scenario("FR_TOURISM", as_of=CHECKED_ON).requirements
    expired = registry.get_scenario("FR_TOURISM", as_of=EXPIRED_ON)
    assert expired.entry_mode == "needs_verification"
    assert all(source.status == "needs_verification" for source in expired.sources)
    expired_rules = registry.get_ruleset(registry.active_version, as_of=EXPIRED_ON)
    assert all(source.status != "source_checked" for source in expired_rules.sources)
    for operation in (
        lambda: registry.get_ruleset("not-installed"),
        lambda: registry.get_sources(["not-known"]),
    ):
        with pytest.raises(AppError) as missing:
            operation()
        assert missing.value.status_code == 404
    au = registry.get_scenario("AU_TOURISM", as_of=CHECKED_ON)
    source = next(s for s in au.sources if s.id == "AU_600")
    assert source.status == "needs_verification" and source.checked_at is None
    assert source.attempted_at and source.notes
    with pytest.raises(ValueError):
        Source(id="bad", url="https://example.com", authority="fixture", status="source_checked")


@pytest.mark.parametrize("mutation", [
    "executable_kind", "unknown_field", "missing_source", "unknown_requirement",
    "version_mismatch", "duplicate_rule", "empty_logic", "bad_parameters",
])
def test_invalid_catalog_fails_at_load(tmp_path, mutation):
    root = tmp_path / "catalog"
    shutil.copytree(files("data.rules.destinations"), root)
    path = root / "2026-10-25.1" / "FR.json"
    content = json.loads(path.read_text())
    if mutation == "executable_kind":
        content["rules"][0]["kind"] = "eval"
    elif mutation == "unknown_field":
        content["rules"][0]["applies_if"] = {"field": "profile.__class__", "op": "eq", "value": "unsafe"}
    elif mutation == "missing_source":
        content["rules"][0]["source_ids"] = ["nonexistent"]
    elif mutation == "unknown_requirement":
        content["rules"][0]["requirement_id"] = "not-defined"
    elif mutation == "version_mismatch":
        content["rules"][0]["version"] = "other"
    elif mutation == "duplicate_rule":
        content["rules"].append(content["rules"][0])
    elif mutation == "empty_logic":
        content["rules"][0]["applies_if"] = {"all": []}
    elif mutation == "bad_parameters":
        content["rules"][0]["parameters"] = {"shell": "must never run"}
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError):
        FileRuleRegistry(root)


def test_path_traversal_and_duplicate_json_keys_are_rejected(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text('{"schema_version":1,"active_version":"../bad","versions":["../bad"]}')
    with pytest.raises(ValueError, match="directory"):
        FileRuleRegistry(root)
    manifest.write_text('{"schema_version":1,"schema_version":1,"active_version":"v1","versions":["v1"]}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        FileRuleRegistry(root)
