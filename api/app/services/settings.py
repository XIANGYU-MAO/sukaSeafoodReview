from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, SystemSetting
from app.schemas.admin import SystemSettingsPatchRequest, SystemSettingsResponse


SYSTEM_SETTINGS_ID = 1
DEFAULT_LOGIN_NAME_MODE = "choices"
DEFAULT_REVIEWER_TEAM_PROGRESS_VISIBLE = True


async def get_system_settings(session: AsyncSession) -> SystemSettingsResponse:
    record = await session.get(SystemSetting, SYSTEM_SETTINGS_ID)
    if record is None:
        return SystemSettingsResponse(
            login_name_mode=DEFAULT_LOGIN_NAME_MODE,
            reviewer_team_progress_visible=DEFAULT_REVIEWER_TEAM_PROGRESS_VISIBLE,
        )
    return SystemSettingsResponse(
        login_name_mode=record.login_name_mode,
        reviewer_team_progress_visible=record.reviewer_team_progress_visible,
    )


async def update_system_settings(
    session: AsyncSession,
    actor_id: UUID,
    payload: SystemSettingsPatchRequest,
) -> SystemSettingsResponse:
    record = await session.scalar(
        select(SystemSetting)
        .where(SystemSetting.id == SYSTEM_SETTINGS_ID)
        .with_for_update()
    )
    if record is None:
        record = SystemSetting(id=SYSTEM_SETTINGS_ID)
        session.add(record)
        await session.flush()

    before = {
        "login_name_mode": record.login_name_mode,
        "reviewer_team_progress_visible": record.reviewer_team_progress_visible,
    }
    record.login_name_mode = payload.login_name_mode
    record.reviewer_team_progress_visible = payload.reviewer_team_progress_visible
    after = {
        "login_name_mode": record.login_name_mode,
        "reviewer_team_progress_visible": record.reviewer_team_progress_visible,
    }
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="system_settings.update",
            object_type="system_settings",
            object_id=str(SYSTEM_SETTINGS_ID),
            reason=payload.reason,
            before_json=before,
            after_json=after,
        )
    )
    await session.commit()
    return SystemSettingsResponse(**after)
