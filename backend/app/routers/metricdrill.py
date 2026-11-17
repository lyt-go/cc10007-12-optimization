"""指标归因接口：维护归因分析，覆盖发起分析、确认结论、归档分析等动作。

指标下钻清单是归因建模增量重算结果的落点：待处理明细、清单复核与
明细页重开都带核验码，方便按明细记录追溯。
注意：静态路径必须声明在 `/{entry_id}` 之前，否则会被路径参数截获
（此前的 /export 就因此被 shadow，属于调用链上的数据断点）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload
from app.services.attrrecalc import BIZ_CODE, LIST_CODE, recalc_service
from app.services.metricdrill import MetricdrillService

router = APIRouter(prefix="/api/metricdrill", tags=["指标归因"])

service = MetricdrillService()

LIST_FIELDS = ["分析编号", "目标指标", "波动方向", "波动幅度", "维度下钻路径", "贡献度排名", "根因假设", "验证结果"]
STATUSES = ["草稿", "分析中", "已确认", "已归档"]


# ---------------- 指标下钻清单（静态路径，先于 /{entry_id} 声明） ----------------


@router.get("/pending-details")
def pending_details() -> dict[str, Any]:
    """指标下钻待处理明细：归因增量明细数据集已投递、尚未确认的归因分析。"""
    return recalc_service.pending_drill_details()


@router.get("/drill-list")
def drill_list() -> dict[str, Any]:
    """指标下钻清单：归因建模（METR-0002）增量重算结果的落点，附清单复核核验码。"""
    return recalc_service.drill_list()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出指标归因清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "metricdrill", "total": total, "items": items}


# ---------------- 归因分析基础接口 ----------------


@router.get("")
def list_entries(
    keyword: str | None = Query(default=None, description="按分析编号检索"),
    status: str | None = Query(default=None, description="草稿、分析中、已确认、已归档"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """按分析编号与状态过滤指标归因列表；关联增量明细的记录带清单复核核验码。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return {
        "items": recalc_service.decorate_drill_items(items),
        "total": total,
        "page": page,
        "size": size,
        "清单核验码": LIST_CODE,
        "业务核验码": BIZ_CODE,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因分析明细；重新打开页面时带回关联的归因增量明细。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因分析 {entry_id} 不存在或已归档")
    return {**entry, **recalc_service.drill_detail_extra(str(entry.get("分析编号", "")))}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因分析，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因分析已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条归因分析执行发起分析、确认结论、归档分析；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
