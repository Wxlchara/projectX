"""Read-only catalog, loaded and validated at startup. No network or eval."""

import json
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.common.errors import AppError
from app.common.models import DataQuality, Destination, EntryMode, Model, Ruleset, Source
from app.rules.schema import CountryDefinition

COUNTRIES = {"FR", "ES", "JP", "IN", "EG", "ZA", "US", "CA", "BR", "AR", "AU", "NZ"}


class CatalogManifest(Model):
    schema_version: Literal[1]
    active_version: str
    versions: list[str] = Field(min_length=1)


class VersionManifest(Model):
    version: str
    countries: list[str] = Field(min_length=12, max_length=12)


def source_quality(source: Source, as_of: date | None = None) -> DataQuality:
    today = as_of or datetime.now(timezone.utc).date()
    if source.status == DataQuality.CONFLICTING:
        return DataQuality.CONFLICTING
    if (
        source.status != DataQuality.SOURCE_CHECKED or source.checked_at is None
        or source.review_after is None or not source.evidence
        or source.checked_at.date() > today or source.review_after < today
    ):
        return DataQuality.NEEDS_VERIFICATION
    return DataQuality.SOURCE_CHECKED


def effective_scenario(scenario, as_of=None):
    result = scenario.model_copy(deep=True)
    for source in result.sources:
        source.status = source_quality(source, as_of)
    entry_sources = [s for s in result.sources if s.id in result.entry_source_ids]
    if any(s.status != DataQuality.SOURCE_CHECKED for s in entry_sources):
        result.entry_mode = EntryMode.NEEDS_VERIFICATION
    qualities = {s.status for s in result.sources}
    result.data_quality = (
        DataQuality.CONFLICTING if DataQuality.CONFLICTING in qualities else
        DataQuality.NEEDS_VERIFICATION if result.coverage == "partial" or qualities != {DataQuality.SOURCE_CHECKED}
        else DataQuality.SOURCE_CHECKED
    )
    return result


def read_json(path):
    # Duplicate keys could silently replace a condition/source in ordinary JSON.
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


class FileRuleRegistry:
    def __init__(self, root: Path | None = None):
        root = root if root is not None else files("data.rules.destinations")
        manifest = CatalogManifest.model_validate(read_json(root.joinpath("manifest.json")))
        if manifest.active_version not in manifest.versions or len(set(manifest.versions)) != len(manifest.versions):
            raise ValueError("Invalid active/duplicate ruleset version")
        self.active_version = manifest.active_version
        self._catalogs = {}
        self._rulesets = {}
        for version in manifest.versions:
            if not version or Path(version).name != version or version in (".", ".."):
                raise ValueError("Invalid ruleset directory")
            folder = root.joinpath(version)
            version_manifest = VersionManifest.model_validate(read_json(folder.joinpath("manifest.json")))
            if version_manifest.version != version:
                raise ValueError("Ruleset version mismatch")
            catalog, sources, rules = {}, {}, []
            if len(set(version_manifest.countries)) != 12:
                raise ValueError("Duplicate country file")
            for filename in version_manifest.countries:
                if Path(filename).name != filename or not filename.endswith(".json"):
                    raise ValueError("Invalid country filename")
                definition = CountryDefinition.model_validate(read_json(folder.joinpath(filename)))
                scenario = definition.scenario
                if scenario.version != version or scenario.id in catalog:
                    raise ValueError("Scenario version/identifier mismatch")
                catalog[scenario.id] = definition
                for source in scenario.sources:
                    if source.id in sources and source != sources[source.id]:
                        raise ValueError("Conflicting definitions of one source ID")
                    sources[source.id] = source
                rules.extend(definition.rules)
            if {item.scenario.destination_country for item in catalog.values()} != COUNTRIES:
                raise ValueError("Catalog must contain exactly the 12 supported countries")
            if len({rule.id for rule in rules}) != len(rules):
                raise ValueError("Duplicate rule IDs across scenarios")
            self._catalogs[version] = catalog
            self._rulesets[version] = Ruleset(version=version, rules=rules, sources=list(sources.values()))

    def get_scenario(self, scenario_id: str, *, version: str | None = None, as_of: date | None = None):
        catalog = self._catalogs.get(version if version is not None else self.active_version)
        if catalog is None:
            raise AppError(404, "ruleset_not_found", "Версия правил не найдена.")
        definition = catalog.get(scenario_id)
        if definition is None:
            raise AppError(404, "scenario_not_found", "Сценарий не найден.")
        return effective_scenario(definition.scenario, as_of)

    def get_ruleset(self, version: str, *, as_of: date | None = None) -> Ruleset:
        ruleset = self._rulesets.get(version)
        if ruleset is None:
            raise AppError(404, "ruleset_not_found", "Версия правил не найдена.")
        result = ruleset.model_copy(deep=True)
        for source in result.sources:
            source.status = source_quality(source, as_of)
        return result

    def get_sources(self, source_ids: list[str], *, version: str | None = None) -> list[Source]:
        ruleset = self._rulesets.get(version if version is not None else self.active_version)
        if ruleset is None:
            raise AppError(404, "ruleset_not_found", "Версия правил не найдена.")
        sources = {source.id: source for source in ruleset.sources}
        if not set(source_ids) <= sources.keys():
            raise AppError(404, "source_not_found", "Источник не найден.")
        result = []
        for source_id in source_ids:
            source = sources[source_id].model_copy(deep=True)
            source.status = source_quality(source)
            result.append(source)
        return result

    def destinations(self, *, as_of: date | None = None) -> list[Destination]:
        result = []
        for definition in self._catalogs[self.active_version].values():
            scenario = effective_scenario(definition.scenario, as_of)
            result.append(Destination(
                country=scenario.destination_country, name=definition.name,
                scenario_id=scenario.id, version=scenario.version,
                entry_mode=scenario.entry_mode, data_quality=scenario.data_quality,
            ))
        return result
