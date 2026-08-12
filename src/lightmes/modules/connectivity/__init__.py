from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """Register connectivity admin routes. Filled in Task 3."""
    # 触发 models 加载（确保 Base.metadata 注册）
    from lightmes.modules.connectivity import models  # noqa: F401
    # 后续 task 填充 router 注册
