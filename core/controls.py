"""Global owner controls for autonomous Harold activity."""

from sqlalchemy.orm import Session

from core.models import OwnerActivity, SystemControl, utcnow


PAUSE_KEY = "harold_paused"


class ControlService:
    def __init__(self, session: Session):
        self.session = session

    def is_paused(self) -> bool:
        control = self.session.get(SystemControl, PAUSE_KEY)
        return bool(control and control.enabled)

    def set_paused(self, paused: bool, changed_by: str = "owner", reason: str = "") -> None:
        control = self.session.get(SystemControl, PAUSE_KEY)
        if control is None:
            control = SystemControl(key=PAUSE_KEY)
            self.session.add(control)
        control.enabled = bool(paused)
        control.changed_by = changed_by
        control.changed_at = utcnow()
        self.session.add(OwnerActivity(
            action="pause_harold" if paused else "resume_harold",
            outcome="completed",
            target_type="system_control",
            target_id=PAUSE_KEY,
            detail={"reason": reason},
        ))
