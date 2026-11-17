"""归因建模接口：维护归因模型，覆盖提交模型评审、发布模型、下线模型等动作。

增量重算链路也挂在这里：触点权重变化后的刷新、结束操作、失败重试与明细追溯。
注意：静态路径必须声明在 `/{entry_id}` 之前，否则会被路径参数截获
（此前的 /export 就因此被 shadow，属于调用链上的数据断点）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.attrmodel import AttrmodelService
from app.services.attrrecalc import recalc_service

router = APIRouter(prefix="/api/attrmodel", tags=["归因建模"])

service = AttrmodelService()

LIST_FIELDS = ["模型编号", "模型名称", "归因逻辑", "触点权重分布", "衰减天数", "转化窗口", "直接贡献率", "间接贡献率"]
STATUSES = ["草稿", "模型评审中", "已发布", "已下线"]


# ---------------- 增量重算链路（静态路径，先于 /{entry_id} 声明） ----------------


@router.post("/refresh")
def refresh_incremental() -> dict[str, Any]:
    """触点权重增量重算：按差异读取权重变化记录，只重算受影响指标。

    重复刷新返回同一会话的持久结果，不会重复写归因增量明细。
    """
    return recalc_service.run_refresh()


@router.get("/refresh")
def refresh_state() -> dict[str, Any]:
    """重新打开增量重算页面：返回当前会话的持久化状态，与刷新返回一致。"""
    return recalc_service.get_state()


@router.post("/refresh/finish")
def refresh_finish() -> dict[str, Any]:
    """结束操作：关闭当前增量重算会话，结果保持持久可读。"""
    result = recalc_service.finish()
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@router.post("/refresh/retry")
def refresh_retry() -> dict[str, Any]:
    """失败重试：只补写未完成的归因增量明细，已完成记录不改动。"""
    return recalc_service.retry_failed()


@router.get("/delta-details")
def delta_details(metric: str | None = Query(default=None, description="按分析编号过滤")) -> dict[str, Any]:
    """归因增量明细数据集：全量可追溯明细，兼容既有业务记录。"""
    return recalc_service.list_delta(metric)


@router.get("/affected-metrics")
def affected_metrics() -> dict[str, Any]:
    """受影响指标清单：与已完成验证结果分开放置。"""
    return recalc_service.list_impact()


@router.get("/verifications")
def verifications() -> dict[str, Any]:
    """已完成验证结果：历史验证只读，增量重算不改写。"""
    return recalc_service.list_verify()


@router.get("/contribution-compare")
def contribution_compare() -> dict[str, Any]:
    """对比受影响与未受影响指标的直接贡献率、间接贡献率和排名。"""
    return recalc_service.contribution_compare()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出归因建模清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "attrmodel", "total": total, "items": items}


# ---------------- 归因模型基础接口 ----------------


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模型编号检索"),
    status: str | None = Query(default=None, description="草稿、模型评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模型编号与状态过滤归因建模列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因模型明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因模型 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因模型，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因模型已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条归因模型执行提交模型评审、发布模型、下线模型；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
