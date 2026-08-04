from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from lightmes.config import get_settings
from lightmes.shared.base import Base
# 导入所有模型，确保它们注册到 Base.metadata（Task 5 起逐步加）
from lightmes.modules.auth import models as _auth_models  # noqa: F401
from lightmes.modules.masterdata import models as _masterdata_models  # noqa: F401
from lightmes.modules.production import models as _production_models  # noqa: F401
from lightmes.modules.trace import models as _trace_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
