from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSON, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|partial|feeding|moderation|signing|published|error
    source_filename: Mapped[str] = mapped_column(String(256))
    feed_id: Mapped[str] = mapped_column(String(64), default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at = mapped_column(DateTime, server_default=func.now())


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("nkmt.batches.id"))
    article: Mapped[str] = mapped_column(String(64), unique=True)
    gtin: Mapped[str] = mapped_column(String(32), default="")
    good_id: Mapped[str] = mapped_column(String(64), default="")
    tnved: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String)
    cat_id: Mapped[str] = mapped_column(String(64), default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|fed|moderation|notsigned|signing|published|error|errors|error_sign
    error_text: Mapped[str] = mapped_column(String, default="")
    is_set: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at = mapped_column(DateTime, server_default=func.now())
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SetItem(Base):
    """Компонент набора (у Cards с is_set=true).

    Ссылка на компонент — артикул нашей карточки (article_src; резолвится в
    gtin на подаче — компонент может быть ещё без GTIN) и/или внешний GTIN.
    Дубли компонентов проверяются в сервисе: UNIQUE-констрейнт не подходит
    из-за пустых gtin у ссылок по артикулу.
    """
    __tablename__ = "set_items"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("nkmt.cards.id", ondelete="CASCADE"))
    gtin: Mapped[str] = mapped_column(String(32), default="")
    article_src: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    created_at = mapped_column(DateTime, server_default=func.now())


class Declaration(Base):
    """Декларация/сертификат соответствия — источник подстановки в карточки НК.

    Ядро (номер+дата+тип) заводится оператором вручную;rich-поля (статус,
    срок, продукция, список ТНВЭД, техрегламенты, заявитель, изготовитель)
    обогащаются из ЧЗ методом /true-api/rd/list (кнопка «Проверить в ЧЗ»
    или автоматически после добавления). tnved_list — допустимые коды
    «6505003000, 6505009000» из ответа ЧЗ, контроль ТНВЭД строк импорта.
    """
    __tablename__ = "declarations"
    __table_args__ = (
        UniqueConstraint("doc_number", "doc_date", name="uq_doc_pair"),
        {"schema": "nkmt"},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_number: Mapped[str] = mapped_column(String)
    doc_date: Mapped[str] = mapped_column(String(10))  # ISO 'YYYY-MM-DD'
    doc_type: Mapped[str] = mapped_column(String(16), default="declaration")  # declaration|certificate
    title: Mapped[str] = mapped_column(String, default="")
    # --- обогащение из ЧЗ (rd/list), пусто = не проверялась ---
    status: Mapped[str] = mapped_column(String(64), default="", server_default="")
    date_to: Mapped[str] = mapped_column(String(10), default="", server_default="")
    product_name: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    tnved_list: Mapped[list] = mapped_column(JSONB, default=list,
                                             server_default=text("'[]'::jsonb"))
    techregs: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    applicant: Mapped[str] = mapped_column(String(512), default="", server_default="")
    manufacturer: Mapped[str] = mapped_column(String(512), default="", server_default="")
    checked_at = mapped_column(DateTime, nullable=True)


class Producer(Base):
    """Справочник производителей: каноническое наименование для атрибута 2503
    карточек и подстановок правил/дефолтов, ИНН и тип для контекста НК/РД.
    Правила ссылаются на производителя текстом (без FK) — удаление записи
    справочника не ломает существующие подстановки."""
    __tablename__ = "producers"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True)  # как в 2503 карточки
    inn: Mapped[str] = mapped_column(String(12), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(16), default="", server_default="")  # entrepreneur|company
    note: Mapped[str] = mapped_column(String(512), default="", server_default="")
    created_at = mapped_column(DateTime, server_default=func.now())


class Rule(Base):
    """Правило РД: подстановка декларации/производителя при импорте выгрузки.

    Условие — точный бренд и/или список видов товара (хотя бы одно непустое,
    CHECK); пустое поле условия = «любой». Вид товара — СПИСОК: правило
    срабатывает, если вид строки входит в него (бренд и вид — casefold).
    Из подошедших правил выигрывает то, у кого больше непустых условий, при
    равенстве — больший id (resolve.match_rule). Приоритет значений:
    файл > правило > дефолт. fields — дополнительные поля карточки
    (whitelist = parse.RULE_FIELDS, например {"size": "ONE SIZE"} для шапок).
    """

    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint("btrim(brand) <> '' OR jsonb_array_length(product_types) > 0",
                        name="ck_rules_condition"),
        {"schema": "nkmt"},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String, default="")            # "" = любой бренд
    product_types: Mapped[list] = mapped_column(JSONB, default=list)  # [] = любой вид товара
    declaration_id: Mapped[int] = mapped_column(
        ForeignKey("nkmt.declarations.id", ondelete="RESTRICT"))
    producer: Mapped[str] = mapped_column(String, default="")
    fields: Mapped[dict] = mapped_column(JSONB, default=dict,
                                         server_default=text("'{}'::jsonb"))


class BrandCache(Base):
    __tablename__ = "brand_cache"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    brand_id: Mapped[int] = mapped_column(Integer)
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
