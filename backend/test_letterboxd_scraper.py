"""Parser tests for the Letterboxd seed harvester.

The critical regression: ``rated-10`` must parse as 5.0, never as 0.5.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from letterboxd_common import (  # noqa: E402
    is_challenge,
    next_page_url,
    parse_grid,
    parse_profile_films,
    pseudonym,
)

GRID_HTML = """
<ul>
  <li class="griditem">
    <div data-item-slug="ten" data-item-name="Ten (2010)"></div>
    <span class="rating -micro rated-10"></span>
  </li>
  <li class="griditem">
    <div data-item-slug="one" data-item-name="One (2001)"></div>
    <span class="rating -micro rated-1"></span>
  </li>
  <li class="griditem">
    <div data-item-slug="unrated" data-item-name="Unrated (2020)"></div>
  </li>
  <li class="griditem">
    <div data-item-slug="dupe" data-item-name="Dupe (1999)"></div>
  </li>
  <li class="griditem">
    <div data-item-slug="dupe" data-item-name="Dupe (1999)"></div>
    <span class="rating rated-8"></span>
  </li>
</ul>
"""


def test_rated_10_is_five_stars_not_half_a_star():
    rows = {row["slug"]: row for row in parse_grid(GRID_HTML)}
    assert rows["ten"]["rating"] == 5.0
    assert rows["one"]["rating"] == 0.5
    assert rows["unrated"]["rating"] is None


def test_title_year_and_duplicate_prefers_rated_version():
    rows = {row["slug"]: row for row in parse_grid(GRID_HTML)}
    assert rows["ten"]["title"] == "Ten"
    assert rows["ten"]["year"] == 2010
    assert rows["dupe"]["rating"] == 4.0
    assert len(rows) == 4


def test_profile_films_count_is_commasafe():
    html = '<div class="profile-statistic">1,473 Films</div>'
    assert parse_profile_films(html) == 1473


def test_next_page_prefers_forward_link():
    html = '<a class="next" href="/thomasflight/likes/films/page/2/">Older</a>'
    assert (
        next_page_url(html, "https://letterboxd.com/thomasflight/likes/films/")
        == "https://letterboxd.com/thomasflight/likes/films/page/2/"
    )


def test_pseudonym_is_stable_and_prefixed():
    first = pseudonym("ThomasFlight")
    second = pseudonym("thomasflight")
    assert first == second
    assert first.startswith("lb_")
    assert len(first) == 13


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self.html_content = body


def test_login_captcha_attribute_is_not_an_antibot_page():
    assert not is_challenge(
        FakeResponse(200, '<form data-captcha-action="signin"></form>')
    )


def test_real_cloudflare_interstitial_is_detected():
    assert is_challenge(FakeResponse(200, "<title>Just a moment...</title>"))
    assert is_challenge(FakeResponse(403, "Forbidden"))
