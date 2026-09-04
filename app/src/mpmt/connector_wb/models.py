from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from mpmt.db import Base


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
