"""The static animal list: lookup, home, and plain words."""

from __future__ import annotations

import pytest

from builder.animal_list import find_by_id, habitat_word, short_name

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
