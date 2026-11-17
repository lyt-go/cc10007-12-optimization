"""归因增量重算：触点权重变化后的受影响指标识别、贡献度计算与可追溯明细落库。

设计要点：
- 只按（归因模型， 下钻路径）识别受影响指标并去重，未受影响指标的贡献度排名与
  历史验证结果不参与刷新；
- 归因增量明细数据集只追加、按追溯号去重，并发/重复写入不会留下第二份；
- 受影响指标清单与已完成验证结果分表存放，失败重试只补写未完成指标；
- 新旧权重版本覆盖同一目标指标既有窗口时，同一指标只应用一套完整归因模型
  （生效窗口止更晚的版本），另一套窗口的明细标记为未生效，仅留档可追溯。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.store import store

# 核验码：本业务各环节的操作凭证，前后端与核对流程共用同一组常量。
BIZ_CODE = "125fadcf"      # 本业务核验码
REFRESH_CODE = "125b8f0a"  # 归因建模刷新核验码
FINISH_CODE = "12351935"   # 结束操作核验码
PAGE_CODE = "123a3a36"     # 重新打开页面核验码
LIST_CODE = "12536ee2"     # 清单复核关联记录核验码

WEIGHT_TABLE = "attrweight"    # 触点权重变化记录（增量重算输入）
DELTA_TABLE = "attrdelta"      # 归因增量明细数据集
IMPACT_TABLE = "attrimpact"    # 受影响指标清单（与已完成验证结果分开放置）
VERIFY_TABLE = "attrverify"    # 已完成验证结果（历史验证只读）
SESSION_TABLE = "attrsession"  # 增量重算会话
DRILL_TABLE = "metricdrill"    # 指标下钻清单

DECAY = 0.4  # 间接贡献衰减系数：间接贡献率 = 直接贡献率 * DECAY

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _weight_diff(change: dict[str, Any]) -> float:
    """按差异读取触点权重配置：新权重 - 旧权重。"""
    return round(_to_float(change.get("新权重")) - _to_float(change.get("旧权重")), 6)


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _next_code(table: str, field: str, prefix: str) -> str:
    numbers = []
    for row in store.rows(table):
        code = str(row.get(field, ""))
        if code.startswith(prefix + "-"):
            try:
                numbers.append(int(code.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{(max(numbers, default=0) + 1):04d}"


class AttrRecalcService:
    """触点权重变化后的归因增量重算链路。"""

    # ---------------- 基础读取 ----------------

    def _pending_changes(self) -> list[dict[str, Any]]:
        return [row for row in store.rows(WEIGHT_TABLE) if row.get("status") == "待应用"]

    def _current_session(self) -> dict[str, Any] | None:
        sessions = store.rows(SESSION_TABLE)
        return sessions[-1] if sessions else None

    def delta_records(self, metric: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DELTA_TABLE)
        if metric:
            rows = [row for row in rows if row.get("分析编号") == metric]
        return list(rows)

    def impact_rows(self, batch: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(IMPACT_TABLE)
        if batch:
            rows = [row for row in rows if row.get("批次") == batch]
        return list(rows)

    def verify_rows(self) -> list[dict[str, Any]]:
        """已完成验证结果：只读，历史验证始终可读。"""
        return list(store.rows(VERIFY_TABLE))

    # ---------------- 增量刷新 ----------------

    def run_refresh(self) -> dict[str, Any]:
        """触点权重增量重算：识别受影响指标、计算贡献度、投递指标下钻待处理明细。"""
        with _lock:
            session = self._current_session()
            if session and session.get("status") == "进行中":
                # 重复刷新/并发进入：返回同一会话的持久结果，不重复写明细。
                return self._session_payload(session, reused=True)

            changes = [c for c in self._pending_changes() if _weight_diff(c) != 0]
            session = {
                "id": _next_id(SESSION_TABLE),
                "status": "进行中",
                "pending": True,
                "abnormal": False,
                "会话编号": _next_code(SESSION_TABLE, "会话编号", "RCAL"),
                "业务核验码": BIZ_CODE,
                "刷新核验码": REFRESH_CODE,
                "结束核验码": "",
                "页面核验码": PAGE_CODE,
                "清单核验码": LIST_CODE,
                "创建时间": _now(),
                "结束时间": "",
            }
            session["写入批次"] = f"BATCH-{session['会话编号']}"
            store.rows(SESSION_TABLE).append(session)

            stats = {"生成明细": 0, "跳过重复": 0, "冲突窗口": 0, "未生效明细": 0}
            affected: list[dict[str, Any]] = []
            if changes:
                affected = self._recalc_changes(session, changes, stats)
            session.update(stats)
            session["受影响指标"] = [item["分析编号"] for item in affected]
            return self._session_payload(session, reused=False)

    def _recalc_changes(
        self,
        session: dict[str, Any],
        changes: list[dict[str, Any]],
        stats: dict[str, int],
    ) -> list[dict[str, Any]]:
        batch = str(session["写入批次"])
        # 按（归因模型， 目标指标）去重聚合：同一指标的多条变化只识别一次。
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for change in changes:
            metric = str(change.get("关联分析编号", "")).strip()
            if not metric:
                continue
            by_metric.setdefault(metric, [])
            if change not in by_metric[metric]:
                by_metric[metric].append(change)

        affected: list[dict[str, Any]] = []
        for metric, metric_changes in by_metric.items():
            # 新旧权重版本同时覆盖同一目标指标既有窗口 → 同一指标得到多个重算窗口。
            versions: dict[str, list[dict[str, Any]]] = {}
            for change in metric_changes:
                versions.setdefault(str(change.get("权重版本", "")), []).append(change)
            # 当前根因只能对应一套完整归因模型：取生效窗口止更晚的版本为当前版本。
            applied_version = max(
                versions,
                key=lambda v: (max(str(c.get("生效窗口止", "")) for c in versions[v]), v),
            )
            if len(versions) > 1:
                stats["冲突窗口"] += len(versions) - 1

            for version, version_changes in versions.items():
                applied = version == applied_version
                # 贡献度排名：版本内按新权重降序，权重相同按触点名升序，保证可重放。
                ordered = sorted(
                    version_changes,
                    key=lambda c: (-_to_float(c.get("新权重")), str(c.get("触点", ""))),
                )
                for rank, change in enumerate(ordered, start=1):
                    trace = f"TRACE-{session['会话编号']}-{change.get('变化编号')}"
                    if self._find_delta_by_trace(trace) is not None:
                        stats["跳过重复"] += 1  # 并发写入只留存可追溯的一份
                        continue
                    direct = round(_to_float(change.get("新权重")), 4)
                    record = {
                        "id": _next_id(DELTA_TABLE),
                        "status": "已完成",
                        "pending": False,
                        "abnormal": False,
                        "明细编号": _next_code(DELTA_TABLE, "明细编号", "DLTA"),
                        "变化编号": change.get("变化编号"),
                        "分析编号": metric,
                        "模型编号": change.get("模型编号"),
                        "维度下钻路径": change.get("维度下钻路径"),
                        "触点": change.get("触点"),
                        "旧权重": change.get("旧权重"),
                        "新权重": change.get("新权重"),
                        "权重差异": _weight_diff(change),
                        "直接贡献率": direct,
                        "间接贡献率": round(direct * DECAY, 4),
                        "贡献度排名": rank,
                        "权重版本": version,
                        "重算窗口": f"{change.get('生效窗口起')}~{change.get('生效窗口止')}",
                        "应用状态": "已生效" if applied else "未生效",
                        "追溯号": trace,
                        "写入批次": batch,
                        "业务核验码": BIZ_CODE,
                    }
                    if not applied:
                        stats["未生效明细"] += 1
                    store.rows(DELTA_TABLE).append(record)
                    stats["生成明细"] += 1

            # 受影响指标清单：与已完成验证结果分表存放。
            changed_paths = [str(c.get("维度下钻路径", "")) for c in versions[applied_version]]
            impact = {
                "id": _next_id(IMPACT_TABLE),
                "status": "已识别",
                "pending": True,
                "abnormal": False,
                "批次": batch,
                "分析编号": metric,
                "模型编号": versions[applied_version][0].get("模型编号"),
                "受影响路径": "；".join(dict.fromkeys(changed_paths)),
                "变化编号": "；".join(str(c.get("变化编号")) for c in versions[applied_version]),
                "权重版本": applied_version,
                "识别时间": _now(),
            }
            store.rows(IMPACT_TABLE).append(impact)
            affected.append(impact)
            self._deliver_to_drill(metric, versions[applied_version], applied_version, batch)

            for change in metric_changes:
                change["status"] = "已应用"
                change["pending"] = False
        return affected

    def _deliver_to_drill(
        self,
        metric: str,
        applied_changes: list[dict[str, Any]],
        applied_version: str,
        batch: str,
    ) -> None:
        """把归因增量明细投递到指标下钻待处理明细。

        仅更新引用变化权重的下钻路径对应的贡献度与排名；未受影响指标整行不动，
        当前根因保持原样。
        """
        entry = self._find_drill(metric)
        if entry is None:
            return
        # 目标指标的全部下钻路径：变化路径用新权重，未变化路径按残余权重保留。
        paths = [p for p in str(entry.get("维度下钻路径", "")).split("；") if p]
        changed = {str(c.get("维度下钻路径")): _to_float(c.get("新权重")) for c in applied_changes}
        unchanged = [p for p in paths if p not in changed]
        residual = round(max(1.0 - sum(changed.values()), 0.0) / len(unchanged), 4) if unchanged else 0.0
        weights = {**{p: residual for p in unchanged}, **changed}
        ranking = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
        entry["贡献度排名"] = "；".join(
            f"{path.rsplit('/', 1)[-1]}#{idx}" for idx, (path, _w) in enumerate(ranking, start=1)
        )
        top_path = ranking[0][0] if ranking else ""
        entry["根因假设"] = f"触点权重版本{applied_version}下{top_path.rsplit('/', 1)[-1]}贡献居首，需复核投放结构"
        entry["验证结果"] = f"待验证（增量重算{batch}）"
        entry["当前权重版本"] = applied_version
        entry["关联批次"] = batch
        entry["pending"] = True  # 留在指标下钻待处理明细里

    def _find_drill(self, metric: str) -> dict[str, Any] | None:
        for row in store.rows(DRILL_TABLE):
            if row.get("分析编号") == metric:
                return row
        return None

    def _find_delta_by_trace(self, trace: str) -> dict[str, Any] | None:
        for row in store.rows(DELTA_TABLE):
            if row.get("追溯号") == trace:
                return row
        return None

    # ---------------- 会话状态 ----------------

    def _session_payload(self, session: dict[str, Any], *, reused: bool) -> dict[str, Any]:
        batch = str(session.get("写入批次", ""))
        created = int(session.get("生成明细", 0))
        affected = session.get("受影响指标") or []
        if reused:
            message = f"增量重算会话{session['会话编号']}进行中，返回已持久化结果"
        elif created:
            message = (
                f"归因增量重算完成：受影响指标{len(affected)}个，生成明细{created}条，"
                f"冲突窗口{int(session.get('冲突窗口', 0))}处"
            )
        else:
            message = "没有待应用的触点权重变化，未触发重算"
        return {
            "ok": True,
            "message": message,
            "业务核验码": BIZ_CODE,
            "核验码": REFRESH_CODE,
            "刷新核验码": REFRESH_CODE,
            "页面核验码": PAGE_CODE,
            "清单核验码": LIST_CODE,
            "会话": dict(session),
            "受影响指标清单": self.impact_rows(batch),
            "增量明细": [r for r in store.rows(DELTA_TABLE) if r.get("写入批次") == batch],
            "未生效分析": [r for r in store.rows(DELTA_TABLE) if r.get("应用状态") == "未生效"],
            "历史验证": self.verify_rows(),
            "贡献对比": self.contribution_compare()["对比"],
        }

    def get_state(self) -> dict[str, Any]:
        """重新打开页面：返回当前会话的持久化状态，与刷新返回保持一致。"""
        session = self._current_session()
        payload: dict[str, Any] = {
            "ok": True,
            "message": "增量重算页面状态已加载",
            "业务核验码": BIZ_CODE,
            "页面核验码": PAGE_CODE,
            "核验码": PAGE_CODE,
            "清单核验码": LIST_CODE,
            "会话": dict(session) if session else None,
            "受影响指标清单": self.impact_rows(str(session.get("写入批次", ""))) if session else [],
            "增量明细": self.delta_records(),
            "未生效分析": [r for r in store.rows(DELTA_TABLE) if r.get("应用状态") == "未生效"],
            "历史验证": self.verify_rows(),
            "贡献对比": self.contribution_compare()["对比"],
        }
        if session and session.get("status") == "进行中":
            payload["刷新核验码"] = REFRESH_CODE
        return payload

    def finish(self) -> dict[str, Any]:
        """结束操作：关闭当前增量重算会话，结果保持持久可读。"""
        with _lock:
            session = self._current_session()
            if session is None or session.get("status") != "进行中":
                return {"ok": False, "message": "没有进行中的增量重算会话", "业务核验码": BIZ_CODE}
            session["status"] = "已结束"
            session["pending"] = False
            session["结束核验码"] = FINISH_CODE
            session["结束时间"] = _now()
            return {
                "ok": True,
                "message": f"增量重算会话{session['会话编号']}已结束，结果已持久化",
                "业务核验码": BIZ_CODE,
                "核验码": FINISH_CODE,
                "结束核验码": FINISH_CODE,
                "会话": dict(session),
            }

    # ---------------- 失败重试 ----------------

    def retry_failed(self) -> dict[str, Any]:
        """失败重试：只补写未完成指标明细，已完成记录一律不动。"""
        with _lock:
            targets = [
                row for row in store.rows(DELTA_TABLE)
                if row.get("status") in ("失败", "待处理")
            ]
            for row in targets:
                change = self._find_change(str(row.get("变化编号", "")))
                new_weight = _to_float(change.get("新权重")) if change else _to_float(row.get("新权重"))
                direct = round(new_weight, 4)
                row["直接贡献率"] = direct
                row["间接贡献率"] = round(direct * DECAY, 4)
                if not str(row.get("贡献度排名") or "").strip():
                    row["贡献度排名"] = self._rank_in_version(row)
                row["status"] = "已完成"
                row["pending"] = False
                row["abnormal"] = False
                row["补写时间"] = _now()
                # 追溯号保持不变：补写仍挂在同一条可追溯明细上。
            return {
                "ok": True,
                "message": f"失败重试完成：补写未完成明细{len(targets)}条，已完成记录未改动",
                "业务核验码": BIZ_CODE,
                "补写条数": len(targets),
                "补写明细": [row.get("明细编号") for row in targets],
            }

    def _find_change(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(WEIGHT_TABLE):
            if row.get("变化编号") == code:
                return row
        return None

    def _rank_in_version(self, record: dict[str, Any]) -> int:
        peers = [
            row for row in store.rows(DELTA_TABLE)
            if row.get("分析编号") == record.get("分析编号")
            and row.get("权重版本") == record.get("权重版本")
        ]
        peers.sort(key=lambda r: (-_to_float(r.get("直接贡献率")), str(r.get("触点", ""))))
        for idx, row in enumerate(peers, start=1):
            if row is record:
                return idx
        return len(peers)

    # ---------------- 对比与清单 ----------------

    def contribution_compare(self) -> dict[str, Any]:
        """对比受影响与未受影响指标的直接贡献率、间接贡献率和排名。

        未生效分析只留档不替换当前结果；已完成的历史验证结果保持可读。
        """
        session = self._current_session()
        batch = str(session.get("写入批次", "")) if session else ""
        impacted = {row.get("分析编号") for row in self.impact_rows(batch)} if batch else set()
        affected: list[dict[str, Any]] = []
        unaffected: list[dict[str, Any]] = []
        for entry in store.rows(DRILL_TABLE):
            metric = str(entry.get("分析编号", ""))
            applied = [
                r for r in store.rows(DELTA_TABLE)
                if r.get("分析编号") == metric and r.get("应用状态") == "已生效"
            ]
            # 最新写入批次（按明细 id 递增）中排名第一的记录代表当前贡献。
            applied.sort(key=lambda r: int(r.get("id", 0)))
            latest_batch = str(applied[-1].get("写入批次", "")) if applied else ""
            current = [r for r in applied if str(r.get("写入批次", "")) == latest_batch]
            current.sort(key=lambda r: (_to_float(r.get("贡献度排名")) or 99.0, str(r.get("触点", ""))))
            top = current[0] if current else {}
            item = {
                "分析编号": metric,
                "目标指标": entry.get("目标指标"),
                "直接贡献率": top.get("直接贡献率", "—"),
                "间接贡献率": top.get("间接贡献率", "—"),
                "贡献度排名": entry.get("贡献度排名"),
                "权重版本": entry.get("当前权重版本", top.get("权重版本", "—")),
                "来源": "增量重算" if metric in impacted else "当前结果",
            }
            (affected if metric in impacted else unaffected).append(item)
        compare = {"受影响指标": affected, "未受影响指标": unaffected}
        return {
            "ok": True,
            "业务核验码": BIZ_CODE,
            "对比": compare,
            "未生效分析": [r for r in store.rows(DELTA_TABLE) if r.get("应用状态") == "未生效"],
            "历史验证": self.verify_rows(),
            "说明": "未生效分析不替换当前结果，历史验证仍可读",
        }

    def list_delta(self, metric: str | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "业务核验码": BIZ_CODE,
            "清单核验码": LIST_CODE,
            "items": self.delta_records(metric),
            "total": len(self.delta_records(metric)),
        }

    def list_impact(self) -> dict[str, Any]:
        rows = self.impact_rows()
        return {"ok": True, "业务核验码": BIZ_CODE, "items": rows, "total": len(rows)}

    def list_verify(self) -> dict[str, Any]:
        rows = self.verify_rows()
        return {"ok": True, "业务核验码": BIZ_CODE, "items": rows, "total": len(rows)}

    # ---------------- 指标下钻侧 ----------------

    def decorate_drill_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清单复核：给有关联增量明细的指标下钻记录挂上复核核验码。"""
        decorated: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            linked = self.delta_records(str(row.get("分析编号", "")))
            if linked:
                row["关联增量明细数"] = len(linked)
                row["清单核验码"] = LIST_CODE
                row["业务核验码"] = BIZ_CODE
            decorated.append(row)
        return decorated

    def drill_detail_extra(self, metric: str) -> dict[str, Any]:
        """重新打开指标下钻明细页：带回关联的归因增量明细，便于复核。"""
        return {
            "页面核验码": PAGE_CODE,
            "业务核验码": BIZ_CODE,
            "关联增量明细": self.delta_records(metric),
        }

    def pending_drill_details(self) -> dict[str, Any]:
        """指标下钻待处理明细：已投递增量结果、尚未确认的归因分析。"""
        items: list[dict[str, Any]] = []
        for entry in store.rows(DRILL_TABLE):
            if not entry.get("pending"):
                continue
            linked = self.delta_records(str(entry.get("分析编号", "")))
            items.append({
                "分析编号": entry.get("分析编号"),
                "目标指标": entry.get("目标指标"),
                "status": entry.get("status"),
                "贡献度排名": entry.get("贡献度排名"),
                "当前权重版本": entry.get("当前权重版本", "—"),
                "关联批次": entry.get("关联批次", "—"),
                "增量明细": linked,
            })
        return {
            "ok": True,
            "业务核验码": BIZ_CODE,
            "清单核验码": LIST_CODE,
            "items": items,
            "total": len(items),
        }

    def drill_list(self) -> dict[str, Any]:
        """指标下钻清单：归因建模结果落点，附清单复核核验码。"""
        rows = self.decorate_drill_items(list(store.rows(DRILL_TABLE)))
        return {
            "ok": True,
            "业务核验码": BIZ_CODE,
            "清单核验码": LIST_CODE,
            "核验码": LIST_CODE,
            "items": rows,
            "total": len(rows),
        }


recalc_service = AttrRecalcService()
