"""Tests for the stated-age reader shared by the caption engine and the loader.

Every value here is a hand-written stand-in for a line of an ``age`` list.
"""

from __future__ import annotations

import unittest
from typing import Optional

from metascan.core.t2i_ages import stated_age


class TestStatedAge(unittest.TestCase):
    def check(self, cases: "tuple[tuple[str, Optional[int]], ...]") -> None:
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(stated_age(value), expected)

    def test_digits(self) -> None:
        self.check(
            (
                ("27-year-old", 27),
                ("45 year old", 45),
                ("70", 70),
                ("108-year-old", 108),
                ("in her 50s", 50),
                ("mid-40s", 40),
            )
        )

    def test_spelled_out_numbers(self) -> None:
        self.check(
            (
                ("forty-year-old", 40),
                ("forty-five-year-old", 45),
                ("Seventy two year old", 72),
                ("eighty-year-old", 80),
                ("nineteen-year-old", 19),
                ("twenty one year old", 21),
                ("FORTY-FIVE-YEAR-OLD", 45),
            )
        )

    def test_the_first_number_wins(self) -> None:
        self.check(
            (
                ("35-to-45-year-old", 35),
                ("forty to fifty-year-old", 40),
                ("age 52 (fifty-two)", 52),
                ("fifty-two or 60", 52),
            )
        )

    def test_no_number_means_no_age(self) -> None:
        self.check(
            (
                ("elderly", None),
                ("middle-aged", None),
                ("someone", None),  # "one" inside a word is not a number
                ("", None),
                ("   ", None),
            )
        )

    def test_a_long_run_of_digits_is_not_an_age(self) -> None:
        self.check((("1990", None), ("12345-year-old", None)))
