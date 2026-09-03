import time
import pytest
from mpmt.nkmt.dicts import (AmbiguousCategory, UnknownBrand, attrs_model,
                             get_defaults, resolve_brand, resolve_category, set_defaults)


class FakeNk:
    def __init__(self, attrs=None, cats=None, brands=None):
        self.attrs, self.cats, self.brands, self.calls = attrs or [], cats or [], brands or [], 0
    def attributes(self, token, tnved, attr_type=None):
        self.calls += 1
        return [{"attr_id": 12}] if attr_type == "m" else [{"attr_id": 23557}]
    def categories(self, token, tnved):
        return self.cats
    def brands(self, token, name):
        return self.brands


def test_attrs_model_cached_24h(db):
    c = FakeNk()
    m1 = attrs_model(db, c, "T", "6109100000")
    m2 = attrs_model(db, c, "T", "6109100000")
    assert m1["m"][0]["attr_id"] == 12 and c.calls == 2  # первый вызов: m+r


def test_brand_cache_hit_and_miss(db):
    from mpmt.nkmt.models import BrandCache
    db.add(BrandCache(name="ycpb", brand_id=2102811)); db.commit()
    assert resolve_brand(db, FakeNk(brands=[]), "T", "YCPB") == 2102811
    with pytest.raises(UnknownBrand):
        resolve_brand(db, FakeNk(brands=[{"brand_id": 1, "brand_name": "OTHER"}]), "T", "ADEL")


def test_category_ambiguous_and_hint(db):
    cats = [{"cat_id": 1, "cat_name": "А"}, {"cat_id": 2, "cat_name": "Б"}]
    with pytest.raises(AmbiguousCategory):
        resolve_category(FakeNk(cats=cats), "T", "6109100000")
    assert resolve_category(FakeNk(cats=cats), "T", "6109100000", "2") == "2"


def test_defaults_kv(db):
    assert get_defaults(db)["brand"] == "YCPB"
    set_defaults(db, {**get_defaults(db), "brand": "ADEL"})
    assert get_defaults(db)["brand"] == "ADEL"
