import csv
import io
import json
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from lightmes.modules.integration.schemas import SyncResult
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductUpsert, BomUpsert, BomItemUpsert,
)


class ErpSyncService(ABC):
    @abstractmethod
    def sync_products(self, raw: bytes) -> SyncResult: ...

    @abstractmethod
    def sync_boms(self, raw: bytes) -> SyncResult: ...


class FileErpSyncService(ErpSyncService):
    """从上传文件导入（模拟金蝶下发）。product→CSV，bom→JSON。
    将来接金蝶 = 另写 KingdeeErpSyncService 读 API，复用 masterdata upsert 逻辑。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.md = MasterDataService(db)

    def sync_products(self, raw: bytes) -> SyncResult:
        result = SyncResult()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for n, row in enumerate(reader, start=2):  # 表头是第1行
            try:
                if not (row.get("erp_ref") or "").strip():
                    raise ValueError("缺少 erp_ref")
                data = ProductUpsert(
                    erp_ref=row["erp_ref"].strip(),
                    code=(row.get("code") or "").strip(),
                    name=(row.get("name") or "").strip(),
                    type=(row.get("type") or "").strip(),
                    unit=(row.get("unit") or "pcs").strip() or "pcs",
                    track_mode=(row.get("track_mode") or "none").strip() or "none",
                    spec=(row.get("spec") or "").strip() or None,
                )
                _, action = self.md.upsert_product(data)
                if action == "created":
                    result.created += 1
                else:
                    result.updated += 1
            except Exception as e:  # 部分成功：坏行跳过
                result.skipped += 1
                result.errors.append(f"行 {n}: {e}")
        return result

    def sync_boms(self, raw: bytes) -> SyncResult:
        result = SyncResult()
        try:
            records = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            result.errors.append(f"JSON 解析失败: {e}")
            return result
        for i, rec in enumerate(records, start=1):
            try:
                data = BomUpsert(
                    erp_ref=rec["erp_ref"],
                    product_code=rec["product_code"],
                    items=[BomItemUpsert(component_code=it["component_code"],
                                         qty=it.get("qty", 1)) for it in rec["items"]],
                )
                _, action = self.md.upsert_bom(data)
                if action == "created":
                    result.created += 1
                else:
                    result.updated += 1
            except Exception as e:
                result.skipped += 1
                result.errors.append(f"第 {i} 条: {e}")
        return result
