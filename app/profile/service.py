"""Persist revisions and invalidate dependent snapshots in one transaction."""

from datetime import datetime, timezone

from app.common.db import Database
from app.common.errors import AppError
from app.common.models import ApplicantProfile, ProfileInput, UserContext


def read_profile(row) -> ApplicantProfile:
    if row is None:
        raise AppError(404, "profile_not_found", "Профиль не найден.")
    data = ProfileInput.model_validate_json(row["data_json"])
    return ApplicantProfile(id=row["id"], revision=row["revision"], **data.model_dump())


class ProfileService:
    def __init__(self, database: Database):
        self.database = database

    def get(self, owner: UserContext) -> ApplicantProfile:
        with self.database.connection() as connection:
            return read_profile(connection.execute(
                "SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),),
            ).fetchone())

    def replace(self, owner: UserContext, data: ProfileInput) -> ApplicantProfile:
        now = datetime.now(timezone.utc)
        invalid = []
        if data.birth_date and data.birth_date > now.date():
            invalid.append("birth_date")
        if data.passport_issued_on and data.passport_expires_on and data.passport_expires_on < data.passport_issued_on:
            invalid.extend(["passport_issued_on", "passport_expires_on"])
        if invalid:
            raise AppError(422, "invalid_profile", "Проверьте даты профиля.", {"fields": invalid})
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),),
            ).fetchone()
            current = read_profile(row)
            if ProfileInput.model_validate_json(row["data_json"]) == data:
                return current
            revision = current.revision + 1
            connection.execute(
                "UPDATE profiles SET data_json = ?, revision = ?, updated_at = ? WHERE owner_id = ?",
                (data.model_dump_json(), revision, now.isoformat(), str(owner.user_id)),
            )
            connection.execute(
                "UPDATE cases SET stale = 1, stale_reason = 'profile_changed', projection_json = NULL, "
                "updated_at = ? WHERE owner_id = ?", (now.isoformat(), str(owner.user_id)),
            )
            # Constant table names only. Preserve evidence, signatures and user edits.
            for table in ("check_results", "routes", "letters"):
                connection.execute(
                    f"UPDATE {table} SET stale = 1 WHERE case_id IN (SELECT id FROM cases WHERE owner_id = ?)",
                    (str(owner.user_id),),
                )
            return ApplicantProfile(id=current.id, revision=revision, **data.model_dump())
