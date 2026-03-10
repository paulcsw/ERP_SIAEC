from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Register all models with Base.metadata
from app.models.user import User, Role, user_roles  # noqa: E402, F401
from app.models.reference import Aircraft, WorkPackage, ShopStream  # noqa: E402, F401
from app.models.ot import OtRequest, OtApproval  # noqa: E402, F401
from app.models.audit import AuditLog  # noqa: E402, F401
from app.models.system_config import SystemConfig  # noqa: E402, F401
from app.models.attendance import ShiftTemplate, ShiftAssignment, AttendanceEvent  # noqa: E402, F401
from app.models.tracking import DailyAssignment, WorklogBlock  # noqa: E402, F401
from app.models.ledger import TimeLedgerDaily, LedgerAllocationDaily  # noqa: E402, F401
