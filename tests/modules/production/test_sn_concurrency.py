import threading
from datetime import datetime
from lightmes.database import SessionLocal, engine
from lightmes.modules.production.models import SnRule
from lightmes.modules.production.sn_generator import SnGenerator


def test_next_sn_unique_under_concurrency():
    """两个真实连接并发抢同一 rule 的流水，行锁必须保证不重号。
    本测试不用 db_session（回滚 fixture 是单连接），直接开真实连接并各自提交，
    结束后清理。"""
    setup = SessionLocal()
    try:
        rule = SnRule(code="CONC", name="c", pattern="C{SEQ:5}", seq_reset="never")
        setup.add(rule)
        setup.commit()
        rule_id = rule.id
    finally:
        setup.close()

    results: list[str] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker() -> None:
        s = SessionLocal()
        try:
            r = s.get(SnRule, rule_id)
            for _ in range(20):
                sn = SnGenerator(s).next_sn(r, datetime(2026, 8, 3))
                s.commit()
                with lock:
                    results.append(sn)
        except Exception as e:  # pragma: no cover
            errors.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 清理该 rule（其余测试不受影响）
    cleanup = SessionLocal()
    try:
        obj = cleanup.get(SnRule, rule_id)
        if obj is not None:
            cleanup.delete(obj)
            cleanup.commit()
    finally:
        cleanup.close()

    assert not errors, f"并发出错: {errors}"
    assert len(results) == 80
    assert len(set(results)) == 80, "SN 出现重复，行锁未生效"
