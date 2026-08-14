import csv
import io
import json
from abc import ABC, abstractmethod
from collections.abc import Callable

from sqlalchemy.orm import Session

from lightmes.modules.integration.schemas import SyncResult
from lightmes.config import get_settings
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    BomItemUpsert,
    BomUpsert,
    ProductUpsert,
)


class ErpSyncService(ABC):
    """ERP 主数据同步基类。

    _apply 保存点引擎对所有数据源（文件/API）通用；子类只负责把各自数据源
    解析为 upsert 输入后调用 _apply，实现真正的部分成功。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.md = MasterDataService(db)

    @abstractmethod
    def sync_products(self, source: object) -> SyncResult:
        """同步产品。文件实现期望 bytes；API 实现可忽略或自行解释 source。"""

    @abstractmethod
    def sync_boms(self, source: object) -> SyncResult:
        """同步 BOM。文件实现期望 bytes；API 实现可忽略或自行解释 source。"""

    def _apply(
        self,
        items: list,
        upsert_fn: Callable,
        label: str,
        *,
        start: int = 1,
    ) -> SyncResult:
        """逐条 upsert：每行一个 SAVEPOINT。

        单行 flush 失败只回滚该 SAVEPOINT，会话保持可用，后续行继续——
        这就是真正的部分成功。upsert 前的校验 ValueError 也在 try 内，一并跳过。
        """
        result = SyncResult()
        for i, item in enumerate(items, start=start):
            try:
                with self.db.begin_nested():  # SAVEPOINT
                    _, action = upsert_fn(item)
                if action == "created":
                    result.created += 1
                elif action == "updated":
                    result.updated += 1
                else:  # 未知动作，按跳过处理避免误计
                    result.skipped += 1
                    result.errors.append(f"{label} {i}: 未知动作 {action!r}")
            except Exception as e:
                # begin_nested 已回滚 SAVEPOINT，异常吞掉，继续下一行
                result.skipped += 1
                result.errors.append(f"{label} {i}: {e}")
        return result


class FileErpSyncService(ErpSyncService):
    """从上传文件导入（模拟金蝶下发）。product→CSV，bom→JSON。
    将来接金蝶 = 另写 KingdeeErpSyncService 读 API，复用 _apply 保存点引擎。"""

    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def sync_products(self, source: object) -> SyncResult:
        if not isinstance(source, bytes):
            raise TypeError("FileErpSyncService.sync_products 需要 bytes")
        reader = csv.DictReader(io.StringIO(source.decode("utf-8-sig")))
        parsed: list[ProductUpsert] = []
        result = SyncResult()
        max_rows = get_settings().max_import_rows
        for n, row in enumerate(reader, start=2):  # 表头是第1行
            if len(parsed) >= max_rows:
                result.skipped += 1
                result.errors.append(f"行 {n}: 超过最大导入行数 {max_rows}")
                break
            try:
                if not (row.get("erp_ref") or "").strip():
                    raise ValueError("缺少 erp_ref")
                parsed.append(ProductUpsert(
                    erp_ref=row["erp_ref"].strip(),
                    code=(row.get("code") or "").strip(),
                    name=(row.get("name") or "").strip(),
                    type=(row.get("type") or "").strip(),
                    unit=(row.get("unit") or "pcs").strip() or "pcs",
                    track_mode=(row.get("track_mode") or "none").strip() or "none",
                    spec=(row.get("spec") or "").strip() or None,
                ))
            except Exception as e:  # 坏行跳过，好行照常
                result.skipped += 1
                result.errors.append(f"行 {n}: {e}")
        applied = self._apply(parsed, self.md.upsert_product, "行", start=2)
        result.created += applied.created
        result.updated += applied.updated
        result.skipped += applied.skipped
        result.errors.extend(applied.errors)
        return result

    def sync_boms(self, source: object) -> SyncResult:
        if not isinstance(source, bytes):
            raise TypeError("FileErpSyncService.sync_boms 需要 bytes")
        try:
            records = json.loads(source.decode("utf-8-sig"))
        except Exception as e:
            result = SyncResult()
            result.errors.append(f"JSON 解析失败: {e}")
            return result
        if not isinstance(records, list):
            result = SyncResult()
            result.errors.append("JSON 顶层必须是数组")
            return result
        parsed: list[BomUpsert] = []
        result = SyncResult()
        max_rows = get_settings().max_import_rows
        for i, rec in enumerate(records, start=1):
            if len(parsed) >= max_rows:
                result.skipped += 1
                result.errors.append(f"第 {i} 条: 超过最大导入条数 {max_rows}")
                break
            try:
                parsed.append(BomUpsert(
                    erp_ref=rec["erp_ref"],
                    product_code=rec["product_code"],
                    items=[BomItemUpsert(component_code=it["component_code"],
                                         qty=it.get("qty", 1)) for it in rec["items"]],
                ))
            except Exception as e:  # 坏条跳过，好条照常
                result.skipped += 1
                result.errors.append(f"第 {i} 条: {e}")
        applied = self._apply(parsed, self.md.upsert_bom, "第", start=1)
        result.created += applied.created
        result.updated += applied.updated
        result.skipped += applied.skipped
        result.errors.extend(applied.errors)
        return result
