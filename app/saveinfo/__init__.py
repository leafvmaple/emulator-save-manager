"""Save-info plugins — identify a save file and extract display-ready facts."""

from app.saveinfo.base import (
    SaveInfoGroup,
    SaveInfoItem,
    SaveInfoPlugin,
    SaveInfoResult,
)
from app.saveinfo.manager import SaveInfoManager, get_save_info_manager

__all__ = [
    "SaveInfoGroup",
    "SaveInfoItem",
    "SaveInfoPlugin",
    "SaveInfoResult",
    "SaveInfoManager",
    "get_save_info_manager",
]
