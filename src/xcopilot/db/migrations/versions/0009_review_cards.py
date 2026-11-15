"""Карточки черновиков в Telegram и комментарий к отказу (ТЗ, этап 12; ADR 0025).

`review_cards` — одна строка на запуск генерации и чат получателя: id сообщения с кнопками
(навигация ◀ ▶ редактирует его), показанный вариант, состояние (queued — ждёт в /queue из-за
дневного предела, shown — показана, expired — старше 48 часов, кнопки сняты) и время показа.
`reviews.comment` — свой текст владельца к отказу «Другое».

Номер 0008 оставлен этапу 9C (договорённость 9C ↔ 12); если 9C добавит 0008, при слиянии
`down_revision` этой миграции переводится на 0008.

Revision ID: 0009
Revises: 0007
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("comment", sa.String(), nullable=True, comment="Комментарий владельца к отказу"),
    )
    op.create_table(
        "review_cards",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "run_id",
            sa.Uuid(),
            nullable=False,
            comment="Запуск генерации, варианты которого в карточке",
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, comment="Чат Telegram получателя"),
        sa.Column(
            "message_id",
            sa.BigInteger(),
            nullable=True,
            comment="Сообщение с кнопками; NULL — ещё не отправлено (в очереди)",
        ),
        sa.Column(
            "variant_no",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Какой вариант сейчас показан",
        ),
        sa.Column(
            "state",
            sa.String(),
            server_default=sa.text("'queued'"),
            nullable=False,
            comment="queued / shown / expired",
        ),
        sa.Column(
            "shown_at", sa.DateTime(timezone=True), nullable=True, comment="Когда показана (UTC)"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'shown', 'expired')", name=op.f("ck_review_cards_state_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_review_cards_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"], name=op.f("fk_review_cards_run_id_generation_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_cards")),
        sa.UniqueConstraint(
            "account_id",
            "run_id",
            "chat_id",
            name=op.f("uq_review_cards_account_id_run_id_chat_id"),
        ),
        comment="Карточки запусков в Telegram: сообщение, вариант, показ и срок (этап 12)",
    )
    op.create_index(
        "ix_review_cards_account_id_state_shown_at",
        "review_cards",
        ["account_id", "state", "shown_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_cards_account_id_state_shown_at", table_name="review_cards")
    op.drop_table("review_cards")
    op.drop_column("reviews", "comment")
