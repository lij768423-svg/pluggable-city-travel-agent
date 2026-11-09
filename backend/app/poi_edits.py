from __future__ import annotations

from copy import deepcopy

from .meal_edits import describe_change, freeze_preview_locations
from .poi_catalog import result_catalog
from .replan import ReplanError, adjust_pace, poi_options, swap_poi
from .schemas import PaceEditPreviewRequest, PoiEditPreviewRequest


def options_for(result: dict, day: int, item_index: int) -> list[dict]:
    with result_catalog(result):
        return poi_options(result, day, item_index)


def build_poi_preview(original: dict, request: PoiEditPreviewRequest) -> tuple[dict, dict]:
    """换景点沿用原行程的目录版本；全局新发布不改变该行程的地点身份和坐标。"""
    with result_catalog(original):
        replaced = original["itinerary"].days[request.day - 1].items[request.item_index].ref_id if (
            1 <= request.day <= len(original["itinerary"].days)
            and request.item_index < len(original["itinerary"].days[request.day - 1].items)) else None
        updated, change = swap_poi(deepcopy(original), request.day, request.item_index, request.poi_id)
        if not any(item.kind == "poi" and item.ref_id == request.poi_id for item in updated["itinerary"].days[request.day - 1].items):
            raise ReplanError(422, "新景点未能排入这一天，原行程保持不变")
        freeze_preview_locations(updated)
        return updated, describe_change(original, updated, request.day, change, "replace_poi", replaced)


def build_pace_preview(original: dict, request: PaceEditPreviewRequest) -> tuple[dict, dict]:
    """调节奏同样沿用原行程的目录版本。用户主动去掉的景点不算“被移出”。"""
    with result_catalog(original):
        updated, change = adjust_pace(deepcopy(original), request.day, request.direction)
        freeze_preview_locations(updated)
        return updated, describe_change(original, updated, request.day, change, f"pace_{request.direction}", change["poi_id"])
