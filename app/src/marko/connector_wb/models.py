from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from marko.db import Base


class WbClientReturn(Base):
    """Возврат покупателя по финансовому отчёту sales (saleID «R…»).

    После чек-сплита 01.09 (FBS-контур) — единственный детектор клиентского
    возврата: op=2 эксайза не приходит, goods-return их не показывает. srid
    R-строки живёт в пространстве документов заказа продажи (order_doc),
    КМ подтягивается журнальной продажей этого документа; строка, приехавшая
    раньше эксайза, сидит в стейджинге (km IS NULL) и применяется при доезде.
    """
    __tablename__ = "client_returns"
    __table_args__ = {"schema": "wb"}

    srid: Mapped[str] = mapped_column(String(96), primary_key=True)
    sale_id: Mapped[str] = mapped_column(String(32), default="")      # «R…»
    rdate: Mapped[str] = mapped_column(String(32), default="")        # дата финансовой операции
    warehouse: Mapped[str] = mapped_column(String(64), default="")    # контур: «Склад WB РФ»=FBO/WB-зона
    order_doc: Mapped[str] = mapped_column(String(64), default="")    # документ заказа продажи
    km: Mapped[str | None] = mapped_column(String(64), nullable=True) # None = ждёт эксайз-продажу
    applied_at = mapped_column(DateTime, nullable=True)               # момент применения к журналу
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class WbReturn(Base):
    """Строка отчёта WB «Возвраты и перемещение товаров» (goods-return).

    КМ в отчёте нет — это физическое движение товара к продавцу; ЧЗ-возврат
    (LP_RETURN) собирается отдельно по excise op=2. srid = «уникальный ID заказа
    на возврат», стабильный ключ строки.
    """
    __tablename__ = "returns"
    __table_args__ = {"schema": "wb"}

    srid: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, default=0)     # сборочное задание
    status: Mapped[str] = mapped_column(String(128), default="")
    expired_dt: Mapped[str] = mapped_column(String(32), default="")  # дедлайн забора с ПВЗ (ISO)
    alerted_new: Mapped[bool] = mapped_column(Boolean, default=False)
    alerted_deadline: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class WbOrder(Base):
    """Реестр заказов WB (/api/v3/orders): order_doc = rid без хвостовых
    счётчиков позиции '.n.m' — эксайз srid и orders rid живут в одном
    пространстве документов, но суффиксы расходятся (инцидент 09.2026).
    Персистентность критична: выкупленный заказ уходит из снапшота раньше,
    чем приезжает эксайз-строка."""
    __tablename__ = "orders"
    __table_args__ = {"schema": "wb"}

    order_doc: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # ID сборочного задания (orders/meta)
    supply_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # поставка (этап отгрузки WB)
    delivery_type: Mapped[str] = mapped_column(String(8), default="")   # fbs/fbo/…
    nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_created_at: Mapped[str] = mapped_column(String(32), default="")  # ISO от WB
    first_seen = mapped_column(DateTime, server_default=func.now())
    last_seen = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
