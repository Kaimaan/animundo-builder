"""The static animal list: lookup, home, and plain words."""

from __future__ import annotations

import pytest

from builder.animal_list import (
    find_by_id,
    habitat_word,
    home_for,
    match_by_name,
    needs_home,
    short_name,
)

ENTRIES = [
    {"id": "fox", "latin": "Vulpes vulpes"},
    {"id": "anaconda", "latin": "Eunectes murinus"},
]


def test_find_by_id() -> None:
    assert find_by_id(ENTRIES, "fox")["latin"] == "Vulpes vulpes"
    with pytest.raises(ValueError, match="not on the animal list"):
        find_by_id(ENTRIES, "dragon")


def test_short_name_is_the_last_word() -> None:
    assert short_name("Red fox") == "Fox"
    assert short_name("Eurasian lynx") == "Lynx"
    assert short_name("Moose") == "Moose"


def test_habitat_word_is_the_last_segment() -> None:
    assert habitat_word("boreal_forest") == "forest"
    assert habitat_word("boreal_lake") == "lake"
    assert habitat_word("rainforest") == "rainforest"


def test_home_falls_back_to_first_habitat() -> None:
    assert (
        home_for({"habitats": ["rainforest"], "latin": "X"}, ["boreal_forest"])
        == "rainforest"
    )
    assert needs_home({"habitats": ["rainforest"]}, ["boreal_forest"]) is True
    assert needs_home({"habitats": ["boreal_forest"]}, ["boreal_forest"]) is False


def test_match_by_name() -> None:
    entries = [{"name": "Mountain hare"}, {"name": "Capybara"}]
    assert match_by_name(entries, "capybara") == {"name": "Capybara"}
    assert match_by_name(entries, "dragon") is None
