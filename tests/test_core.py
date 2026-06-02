import os
import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test_chat")
os.environ.setdefault("SEARCH_URL_1", (
    "https://www.seloger.com/classified-search"
    "?distributionTypes=Rent&estateTypes=Apartment&priceMax=2000"
    "&spaceMin=50&numberOfRoomsMin=2&furnished=No,Not_Applicable"
    "&locations=AD08FR31096,AD08FR36623"
))

from immo_bot.scrapers.seloger import parse_url, build_url, BASE_URL
from immo_bot.core import build_criteria, _load_search_urls, _label_from_params, SEARCH_URLS
from immo_bot.platforms.telegram import _pending_search

_PARAMS = {
    "distributionTypes": "Rent",
    "furnished": "No,Not_Applicable",
    "numberOfRoomsMin": "2",
    "priceMax": "2000",
    "spaceMin": "50",
    "estateTypes": "Apartment",
    "featuresIncluded": "Elevator",
    "locations": "AD08FR31096,AD08FR36623",
}


# ---------------------------------------------------------------------------
# parse_url / build_url round-trip
# ---------------------------------------------------------------------------

def test_parse_url_extracts_fields():
    url = build_url(_PARAMS)
    parsed = parse_url(url)
    for key in _PARAMS:
        assert parsed[key] == _PARAMS[key]


def test_parse_url_roundtrip_is_stable():
    url = build_url(_PARAMS)
    assert build_url(parse_url(url)) == url


def test_parse_url_no_locations():
    url = f"{BASE_URL}?distributionTypes=Rent&priceMax=2000"
    result = parse_url(url)
    assert result["priceMax"] == "2000"
    assert "locations" not in result


def test_build_url_requires_params():
    with pytest.raises((ValueError, TypeError)):
        build_url(None)


def test_build_url_preserves_all_fields():
    url = build_url(_PARAMS)
    assert "locations=AD08FR31096" in url
    assert "estateTypes=Apartment" in url
    assert "priceMax=2000" in url


# ---------------------------------------------------------------------------
# build_criteria
# ---------------------------------------------------------------------------

def test_build_criteria_place_ids():
    c = build_criteria(_PARAMS)
    assert c["location"]["placeIds"] == ["AD08FR31096", "AD08FR36623"]


def test_build_criteria_strips_whitespace():
    p = {**_PARAMS, "locations": " AD08FR31096 , AD08FR36623 "}
    assert build_criteria(p)["location"]["placeIds"] == ["AD08FR31096", "AD08FR36623"]


def test_build_criteria_filters_empty():
    p = {**_PARAMS, "locations": "AD08FR31096,,AD08FR36623,"}
    assert build_criteria(p)["location"]["placeIds"] == ["AD08FR31096", "AD08FR36623"]


def test_build_criteria_scalar_types():
    c = build_criteria(_PARAMS)
    assert c["distributionTypes"] == ["Rent"]
    assert c["estateTypes"] == ["Apartment"]
    assert c["furnished"] == ["No", "Not_Applicable"]
    assert c["numberOfRoomsMin"] == 2
    assert c["priceMax"] == 2000
    assert c["spaceMin"] == 50
    assert c["featuresIncluded"] == ["Elevator"]


def test_build_criteria_no_optional_fields_when_absent():
    p = {k: v for k, v in _PARAMS.items() if k not in ("featuresIncluded", "locationsInBuildingIncluded")}
    c = build_criteria(p)
    assert "featuresIncluded" not in c
    assert "locationsInBuildingIncluded" not in c


# ---------------------------------------------------------------------------
# _load_search_urls
# ---------------------------------------------------------------------------

def test_load_search_urls_reads_numbered_vars(monkeypatch):
    monkeypatch.setenv("SEARCH_URL_1", "https://example.com/1")
    monkeypatch.setenv("SEARCH_URL_2", "https://example.com/2")
    monkeypatch.delenv("SEARCH_URL_3", raising=False)
    assert _load_search_urls() == ["https://example.com/1", "https://example.com/2"]


def test_load_search_urls_stops_at_gap(monkeypatch):
    monkeypatch.setenv("SEARCH_URL_1", "https://example.com/1")
    monkeypatch.delenv("SEARCH_URL_2", raising=False)
    monkeypatch.setenv("SEARCH_URL_3", "https://example.com/3")
    assert _load_search_urls() == ["https://example.com/1"]


def test_load_search_urls_strips_whitespace(monkeypatch):
    monkeypatch.setenv("SEARCH_URL_1", "  https://example.com/1  ")
    monkeypatch.delenv("SEARCH_URL_2", raising=False)
    assert _load_search_urls() == ["https://example.com/1"]


# ---------------------------------------------------------------------------
# _label_from_params
# ---------------------------------------------------------------------------

def test_label_apartment():
    assert _label_from_params({"estateTypes": "Apartment"}) == ("Appartements", "🏢")


def test_label_house():
    assert _label_from_params({"estateTypes": "House"}) == ("Maisons", "🏡")


def test_label_unknown_falls_back():
    label, icon = _label_from_params({"estateTypes": "Studio"})
    assert label == "Studio"
    assert icon == "🔍"


def test_label_missing_key():
    _, icon = _label_from_params({})
    assert icon == "🔍"


# ---------------------------------------------------------------------------
# /search command state (_pending_search)
# ---------------------------------------------------------------------------

def test_pending_search_starts_empty():
    assert isinstance(_pending_search, dict)


def test_pending_search_set_and_cleared():
    _pending_search["test_chat"] = True
    assert "test_chat" in _pending_search
    _pending_search.pop("test_chat", None)
    assert "test_chat" not in _pending_search


def test_search_urls_have_at_least_one_entry():
    assert len(SEARCH_URLS) >= 1


def test_search_menu_labels_are_inferable():
    for url in SEARCH_URLS:
        query = parse_url(url)
        label, icon = _label_from_params(query)
        assert isinstance(label, str) and len(label) > 0
        assert icon in ("🏢", "🏡", "🔍")
