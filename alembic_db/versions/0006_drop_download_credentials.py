"""
Drop download credential storage.

Removes ``downloads.credential_id`` and the ``host_credentials`` table: stored
API keys are replaced by env-var keys plus an on-disk OAuth token store, neither
of which lives in the database.

Revision ID: 0006_drop_download_credentials
Revises: 0005_download_manager
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_drop_download_credentials"
down_revision = "0005_download_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("downloads") as batch_op:
        batch_op.drop_column("credential_id")

    op.drop_index("uq_host_credentials_host", table_name="host_credentials")
    op.drop_table("host_credentials")


def downgrade() -> None:
    op.create_table(
        "host_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column(
            "match_subdomains",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "auth_scheme", sa.String(length=16), nullable=False, server_default="bearer"
        ),
        sa.Column("header_name", sa.String(length=255), nullable=True),
        sa.Column("query_param", sa.String(length=255), nullable=True),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("secret_last4", sa.String(length=4), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "uq_host_credentials_host", "host_credentials", ["host"], unique=True
    )

    with op.batch_alter_table("downloads") as batch_op:
        batch_op.add_column(sa.Column("credential_id", sa.String(length=36), nullable=True))