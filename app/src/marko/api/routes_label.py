"""Этикетка КиЗ: превью (JSON с гридом DataMatrix) и PDF 58×40 к печати.

Оба роута — GET: скачивание с фронта идёт через dl() (fetch+blob, только
GET). Сеть не зовём: имя и решение WB — локальные срезы. Печать не мутирует
контур — скоуп read, но следопырна: каждое скачивание PDF — аудит
label.printed (один КиЗ = один экземпляр товара).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.label import LabelError, build_pdf, prepare
from marko.platform.models import PlatformToken

router = APIRouter(prefix="/v1/label")


@router.get("/preview")
def label_preview(
    sgtin: str = Query(..., min_length=20, max_length=512),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Карточка этикетки для UI: имя, человекочитаемые строки, модули
    GS1-DataMatrix, guard. Блокировка — поле blocked (200): превью обязано
    показать причину, а не упасть. Имя при пустых офлайн-срезах тянется
    живьём из карточки ЧЗ — сеть только с скоупом docs:submit (паттерн
    /v1/identify), ответ кэшируется в kv на неделю."""
    allow_live = "docs:submit" in (tok.scopes or "").split(",")
    try:
        p = prepare(db, sgtin, live=allow_live)
    except LabelError as e:
        raise HTTPException(422, str(e))
    if p["name_live"]:     # аудит только факта сети (паттерн identify.live_meta)
        audit(db, tok.principal_id, "label.cz_name",
              {"km": p["km"], "found": bool(p["name"])})
    return p


@router.get("/print")
def label_print(
    sgtin: str = Query(..., min_length=20, max_length=512),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """PDF-этикетка 58×40 мм (термо-принтер, без сглаживания): тот же грид,
    что в превью. Guard-блокировка → 422 с причиной."""
    allow_live = "docs:submit" in (tok.scopes or "").split(",")
    try:
        p = prepare(db, sgtin, live=allow_live)
    except LabelError as e:
        raise HTTPException(422, str(e))
    if p["blocked"]:
        raise HTTPException(422, p["block_reason"])
    pdf = build_pdf(p)      # сборка до аудита: след — только реальная печать
    audit(db, tok.principal_id, "label.printed",
          {"km": p["km"], "gtin": p["gtin"], "name_source": p["name_source"],
           "modules": p["modules_size"]})
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="kiz-{p["gtin"]}.pdf"'})
