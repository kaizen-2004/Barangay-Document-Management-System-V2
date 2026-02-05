from __future__ import with_statement

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from flask import current_app

# Alembic Config object
config = context.config

# Logging setup
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')

# Set SQLAlchemy URL from Flask app config
config.set_main_option('sqlalchemy.url', current_app.config.get('SQLALCHEMY_DATABASE_URI'))

# Target metadata for 'autogenerate'
# Flask-SQLAlchemy sets this on the extension
target_metadata = current_app.extensions['migrate'].db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
