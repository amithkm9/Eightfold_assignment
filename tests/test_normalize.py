"""Normalizers: the honest-null principle and format consistency."""

from eightfold.normalize.country import to_iso3166
from eightfold.normalize.dates import to_year, to_year_month
from eightfold.normalize.phone import to_e164
from eightfold.normalize.skills import to_canonical


def test_phone_formats_collapse_to_one_e164():
    # Three different formats of the same number must normalize identically (enables dedup).
    assert to_e164("+1 (415) 555-0100")[0] == "+14155550100"
    assert to_e164("(415) 555-0100")[0] == "+14155550100"
    assert to_e164("415.555.0100")[0] == "+14155550100"


def test_phone_unparseable_abstains():
    value, ok = to_e164("call me!")
    assert value is None and ok is False  # wrong-but-confident is worse than empty


def test_dates_year_month_and_year_only():
    assert to_year_month("March 2021") == ("2021-03", True)
    assert to_year_month("2017-06") == ("2017-06", True)
    assert to_year_month("2016") == ("2016", True)  # never fabricate a month
    assert to_year_month("present") == (None, True)


def test_country_iso3166():
    assert to_iso3166("United States")[0] == "US"
    assert to_iso3166("USA")[0] == "US"
    assert to_iso3166("Spain")[0] == "ES"
    assert to_iso3166("Atlantis") == (None, False)  # unknown -> abstain


def test_skills_canonicalization():
    assert to_canonical("golang")[0] == "Go"
    assert to_canonical("k8s")[0] == "Kubernetes"
    assert to_canonical("javascript")[0] == "JavaScript"
    assert to_canonical("postgres")[0] == "PostgreSQL"


def test_year_extraction():
    assert to_year(2016) == (2016, True)
    assert to_year("class of 2016") == (2016, True)
