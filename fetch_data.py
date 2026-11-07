"""
Download the data for this project and check it before saving.

- COVID-19 testing by county, March 2020 to August 2023, from the New York State Department of
  Health open data API (dataset xdss-u53e)
- Yearly county population estimates, 2020 to 2023, from the US Census Bureau

Both files are saved to data/. The checks stop the script if the sources don't line up, rather
than letting a mismatched county drop out silently further down the line.
"""

import io
import json
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

DATA = Path(__file__).parent / "data"
TESTING_API = "https://health.data.ny.gov/resource/xdss-u53e.csv"
CENSUS_URL = ("https://www2.census.gov/programs-surveys/popest/datasets/"
              "2020-2023/counties/totals/co-est2023-alldata.csv")
SHAPEFILE = DATA / "cugir-007865-geojson.json"
PAGE_SIZE = 50_000


def fetch_testing() -> pd.DataFrame:
    """Download county-level rows from the testing API, a page at a time."""
    pages, offset = [], 0
    while True:
        query = urlencode({
            "$select": "test_date, county, new_positives, total_number_of_tests",
            "$where": "geography = 'COUNTY'",
            "$order": "county, test_date",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        })
        with urlopen(f"{TESTING_API}?{query}", timeout=120) as resp:
            page = pd.read_csv(io.BytesIO(resp.read()))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    df = pd.concat(pages, ignore_index=True)
    df["test_date"] = pd.to_datetime(df["test_date"]).dt.date
    return df


def fetch_population() -> pd.DataFrame:
    """Download Census county estimates and reshape to one row per county and year."""
    with urlopen(CENSUS_URL, timeout=120) as resp:
        census = pd.read_csv(io.BytesIO(resp.read()), encoding="latin-1")
    ny = census[(census["STNAME"] == "New York") & (census["SUMLEV"] == 50)].copy()
    ny["county"] = ny["CTYNAME"].str.removesuffix(" County")
    years = [2020, 2021, 2022, 2023]
    pop = ny.melt(id_vars="county", value_vars=[f"POPESTIMATE{y}" for y in years],
                  var_name="year", value_name="population")
    pop["year"] = pop["year"].str.removeprefix("POPESTIMATE").astype(int)
    return pop.sort_values(["county", "year"]).reset_index(drop=True)


def check(testing: pd.DataFrame, pop: pd.DataFrame) -> list:
    """Return a list of problems; an empty list means the data lines up."""
    problems = []
    shapes = {f["properties"]["name"] for f in json.loads(SHAPEFILE.read_text())["features"]}

    for name, counties in [("testing data", set(testing["county"])), ("population data", set(pop["county"]))]:
        if counties != shapes:
            problems.append(f"{name} counties don't match the shapefile: "
                            f"missing {sorted(shapes - counties)}, unexpected {sorted(counties - shapes)}")

    if testing.duplicated(["county", "test_date"]).any():
        problems.append("testing data has more than one row for some county-dates")

    days = testing.groupby("county")["test_date"].agg(["min", "max", "count"])
    expected = (pd.to_datetime(days["max"]) - pd.to_datetime(days["min"])).dt.days + 1
    gaps = days.index[days["count"] != expected].tolist()
    if gaps:
        problems.append(f"testing data has missing days for: {gaps}")

    if (testing[["new_positives", "total_number_of_tests"]] < 0).any().any():
        problems.append("testing data has negative counts")

    if pop.groupby("county").size().ne(4).any() or (pop["population"] <= 0).any():
        problems.append("population data is missing years or has non-positive populations")

    return problems


def main() -> int:
    print("Downloading testing data...")
    testing = fetch_testing()
    print(f"  {len(testing):,} rows, {testing['county'].nunique()} counties, "
          f"{testing['test_date'].min()} to {testing['test_date'].max()}")
    print("Downloading population estimates...")
    pop = fetch_population()
    print(f"  {pop['county'].nunique()} counties, years {pop['year'].min()}-{pop['year'].max()}")

    problems = check(testing, pop)
    if problems:
        print("\nData checks failed, nothing saved:")
        for p in problems:
            print(f"  - {p}")
        return 1

    testing.to_csv(DATA / "ny_covid_testing_by_county.csv", index=False)
    pop.to_csv(DATA / "ny_county_population.csv", index=False)
    print("\nAll checks passed. Saved to data/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
