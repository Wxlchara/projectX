"""Owner-scoped snapshots and CheckResult storage in the existing schema."""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter

from app.auth.ownership import ResourceKind, require_owned
from app.common.errors import AppError
from app.common.models import (
    Case, CaseSnapshot, CheckResult, Finding, InputSignature, Process, Readiness, TripInput,
)
from app.profile.service import read_profile
from .engine import ALGORITHM_VERSION, DocumentChecker, signature

FINDINGS = TypeAdapter(list[Finding])


class ChecksService:
    def __init__(self, database, registry, documents, *, checker=None, rebuilder=None, clock=None):
        self.database = database
        self.registry = registry
        self.documents = documents
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.checker = checker or DocumentChecker(self.clock)
        self.rebuilder = rebuilder

    @staticmethod
    def _read_state(connection, owner, case_id):
        case = dict(require_owned(connection, owner, ResourceKind.CASE, case_id))
        profile = read_profile(connection.execute(
            "SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),),
        ).fetchone())
        documents = connection.execute(
            "SELECT d.id, d.facts_revision FROM case_documents cd JOIN documents d ON d.id = cd.document_id "
            "WHERE cd.case_id = ? AND cd.owner_id = ? AND d.owner_id = ? ORDER BY d.id",
            (str(case_id), str(owner.user_id), str(owner.user_id)),
        ).fetchall()
        latest = connection.execute(
            "SELECT * FROM check_results WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (str(case_id),),
        ).fetchone()
        return {"case": case, "profile": profile,
                "facts": {UUID(row["id"]): row["facts_revision"] for row in documents},
                "latest": dict(latest) if latest is not None else None}

    @staticmethod
    def _signature(state):
        return InputSignature(
            case_revision=state["case"]["revision"], profile_revision=state["profile"].revision,
            facts_revisions=state["facts"], ruleset_version=state["case"]["ruleset_version"],
            algorithm_version=ALGORITHM_VERSION,
        )

    def load_snapshot(self, owner, case_id) -> CaseSnapshot:
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
        if self.registry is None:
            raise AppError(503, "rules_unavailable", "Реестр правил не подключён.")
        row = state["case"]
        if self.registry.active_version != row["ruleset_version"]:
            raise AppError(409, "ruleset_changed", "Набор правил изменился. Сначала пересчитайте дело через пакет 4.")
        # Never nest get_confirmed_facts's short transaction in our transaction.
        facts = self.documents.get_confirmed_facts(owner.user_id, list(state["facts"]))
        with self.database.transaction() as connection:
            current = self._read_state(connection, owner, case_id)
        if (self._signature(state) != self._signature(current)
            or {doc.document_id: doc.facts_revision for doc in facts} != state["facts"]):
            raise AppError(409, "check_inputs_changed", "Входы изменились во время чтения. Повторите проверку.")
        as_of = self.clock().astimezone(timezone.utc).date()
        scenario = self.registry.get_scenario(row["scenario_id"], version=row["ruleset_version"], as_of=as_of)
        ruleset = self.registry.get_ruleset(row["ruleset_version"], as_of=as_of)
        case = Case(
            id=row["id"], scenario_id=row["scenario_id"], trip=TripInput.model_validate_json(row["trip_json"]),
            answers=json.loads(row["answers_json"]), revision=row["revision"], profile_revision=state["profile"].revision,
            ruleset_version=row["ruleset_version"], attached_document_ids=list(state["facts"]),
            checklist=[], readiness=Readiness(), process=Process(), stale=bool(row["stale"]),
            stale_reason=row["stale_reason"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
        # Case readiness/process are deliberately unknown: package 6 never reads
        # a stale projection or invents the projections owned by package 4/9.
        return CaseSnapshot(owner=owner, case=case, profile=state["profile"], scenario=scenario,
                            ruleset=ruleset, documents=facts, input_signature=self._signature(state))

    def run(self, owner, case_id) -> CheckResult:
        snapshot = self.load_snapshot(owner, case_id)
        result = self.checker.check_case(snapshot)
        if result.stale or result.case_id != case_id or result.input_signature != signature(snapshot):
            raise AppError(409, "invalid_check_snapshot", "Снимок проверки не согласован. Обновите дело и повторите проверку.")
        with self.database.transaction() as connection:
            current = self._read_state(connection, owner, case_id)
            if (self._signature(current) != result.input_signature
                or self.registry.active_version != snapshot.case.ruleset_version):
                raise AppError(409, "check_inputs_changed", "Во время проверки изменились документы, профиль или поездка.")
            connection.execute(
                "INSERT INTO check_results (id,case_id,evaluated_at,input_signature_json,findings_json,stale) VALUES (?,?,?,?,?,0)",
                (str(result.id), str(case_id), result.evaluated_at.isoformat(), result.input_signature.model_dump_json(),
                 FINDINGS.dump_json(result.findings).decode()),
            )
            connection.execute(
                "UPDATE cases SET stale = 1, stale_reason = 'checks_updated', projection_json = NULL, updated_at = ? "
                "WHERE id = ? AND owner_id = ?",
                (self.clock().isoformat(), str(case_id), str(owner.user_id)),
            )
        if self.rebuilder is not None:
            try:
                self.rebuilder.rebuild_case(owner, case_id, "checks_updated")
            except Exception as exc:
                logging.getLogger(__name__).warning("Case rebuild deferred: %s", type(exc).__name__)
        return result

    def latest(self, owner, case_id) -> CheckResult:
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
        row = state["latest"]
        if row is None:
            raise AppError(404, "checks_not_found", "Проверки дела ещё не выполнялись.")
        result = CheckResult(
            id=row["id"], case_id=row["case_id"], evaluated_at=row["evaluated_at"],
            input_signature=InputSignature.model_validate_json(row["input_signature_json"]),
            findings=FINDINGS.validate_json(row["findings_json"]), stale=bool(row["stale"]),
        )
        result.stale = (
            result.stale or result.input_signature != self._signature(state)
            or result.evaluated_at.astimezone(timezone.utc).date() != self.clock().astimezone(timezone.utc).date()
            or self.registry is None or self.registry.active_version != state["case"]["ruleset_version"]
        )
        return result
