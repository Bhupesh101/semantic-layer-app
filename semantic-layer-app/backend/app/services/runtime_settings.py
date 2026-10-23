from sqlalchemy.orm import Session

from ..config import RuntimeSettings
from ..models import AppSetting

_KEY = "runtime"


def get_runtime(db: Session) -> RuntimeSettings:
    row = db.get(AppSetting, _KEY)
    return RuntimeSettings(**(row.value if row else {}))


def save_runtime(db: Session, new: RuntimeSettings) -> RuntimeSettings:
    row = db.get(AppSetting, _KEY)
    if row is None:
        row = AppSetting(key=_KEY, value=new.model_dump())
        db.add(row)
    else:
        row.value = new.model_dump()
    db.commit()
    return new
