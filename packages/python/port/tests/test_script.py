import pytest
import json
import pandas as pd
from port.script import (
    extract_search_data,
    is_google_search_url,
    GoogleTakeoutNotFoundError,
    NoGoogleSearchDataError,
    find_google_search_export,
    parse_google_search_json,
    parse_google_search_html,
)
import zipfile
from io import BytesIO, StringIO


@pytest.fixture
def sample_search_data():
    return [
        {
            "header": "Google Suche",
            "title": "Some search",
            "titleUrl": "https://www.google.com/search?q=test+query",
            "time": "2025-02-09T14:39:34.364Z",  # Changed to February
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "Test Result Page",
            "titleUrl": "https://example.com/test",
            "time": "2025-02-09T14:39:42.587Z",  # Changed to February
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "Another search",
            "titleUrl": "https://www.google.com/search?q=another+search&hl=en",
            "time": "2025-02-09T14:40:00.000Z",  # Changed to February
            "products": ["Google Suche"],
        },
    ]


@pytest.fixture
def expected_columns():
    return {
        "searches": ["Datum", "Nummer", "Suchbegriff"],
        "clicks": ["Datum", "Nummer", "Suchergebnis", "Link"],
    }


def assert_dataframe_structure(df, expected_cols, df_type="searches"):
    """Helper function to verify dataframe structure and format"""
    assert list(df.columns) == expected_cols
    if len(df) > 0:
        # Check date format (DD-MM-YYYY)
        assert all(df["Datum"].str.match(r"^[0-9]{2}-[0-9]{2}-[0-9]{4}$"))


def test_extract_search_data(sample_search_data, expected_columns):
    searches_df, clicks_df = extract_search_data(sample_search_data)

    # Test searches dataframe
    assert len(searches_df) == 2
    assert_dataframe_structure(searches_df, expected_columns["searches"])
    assert searches_df["Suchbegriff"].tolist() == ["test query", "another search"]
    assert all(searches_df["Datum"].str.startswith("09-02-2025"))

    # Test clicks dataframe
    assert len(clicks_df) == 1
    assert_dataframe_structure(clicks_df, expected_columns["clicks"])
    assert clicks_df["Suchergebnis"].iloc[0] == "Test Result Page"
    assert clicks_df["Link"].iloc[0] == "https://example.com/test"
    assert clicks_df["Datum"].iloc[0] == "09-02-2025"


def test_extract_search_data_empty(expected_columns):
    searches_df, clicks_df = extract_search_data([])

    assert len(searches_df) == 0
    assert len(clicks_df) == 0
    assert_dataframe_structure(searches_df, expected_columns["searches"])
    assert_dataframe_structure(clicks_df, expected_columns["clicks"])


def test_extract_search_data_malformed(expected_columns):
    malformed_data = [{"header": "Google Suche"}]  # Missing required fields
    searches_df, clicks_df = extract_search_data(malformed_data)

    assert len(searches_df) == 0
    assert len(clicks_df) == 0
    assert_dataframe_structure(searches_df, expected_columns["searches"])
    assert_dataframe_structure(clicks_df, expected_columns["clicks"])


def test_extract_search_data_invalid_input(expected_columns):
    invalid_inputs = [None, "string", 123, {"key": "value"}]

    for data in invalid_inputs:
        searches_df, clicks_df = extract_search_data(data)
        assert len(searches_df) == 0
        assert len(clicks_df) == 0
        assert_dataframe_structure(searches_df, expected_columns["searches"])
        assert_dataframe_structure(clicks_df, expected_columns["clicks"])


def test_extract_search_data_viewed_items_skipped():
    data = [
        {
            "header": "Search",
            "title": "Viewed forbidden knowledge",
            "titleUrl": "https://www.google.com/url?q=forbidden",
            "time": "2025-02-08T09:35:03.562Z",  # Changed to February
            "products": ["Search"],
        },
        {
            "header": "Search",
            "title": "Searched for cookies",
            "titleUrl": "https://www.google.com/search?q=cookies",
            "time": "2025-02-08T09:35:03.562Z",  # Changed to February
            "products": ["Search"],
        },
    ]
    from port.script import extract_search_data

    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 1
    assert searches_df["Suchbegriff"].iloc[0] == "cookies"
    assert len(clicks_df) == 0


def test_extract_search_data_visited_items_processed():
    data = [
        {
            "header": "Search",
            "title": "Visited example website",
            "titleUrl": "https://www.google.com/url?q=https://example.com",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
        {
            "header": "Search",
            "title": "Viewed another website",
            "titleUrl": "https://www.google.com/url?q=https://another.com",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
    ]
    from port.script import extract_search_data

    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 0
    assert len(clicks_df) == 1
    assert clicks_df["Suchergebnis"].iloc[0] == "example website"
    assert clicks_df["Link"].iloc[0] == "https://example.com"


@pytest.mark.parametrize(
    "url, expected_query",
    [
        ("https://www.google.com/search?q=test", "test"),
        ("https://www.google.de/search?q=schnee", "schnee"),
        ("https://www.google.nl/search?q=weer&hl=nl", "weer"),
        ("https://www.google.co.uk/search?q=weather", "weather"),
        (
            "https://www.google.com/search?q=test+query&hl=en&source=hp&ei=123",
            "test query",
        ),
        ("https://www.google.de/search?q=m%C3%BCnchen", "münchen"),
    ],
)
def test_is_google_search_url_valid_cases(url, expected_query):
    is_search, query = is_google_search_url(url)
    assert is_search == True
    assert query == expected_query


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/maps",
        "https://www.google.com",
        "https://www.example.com/search?q=test",
        "https://maps.google.com/search?q=test",
        "https://not-google.com/search?q=test",
        "invalid_url",
        "",
        None,
    ],
)
def test_is_google_search_url_invalid_cases(url):
    is_search, query = is_google_search_url(url)
    assert is_search == False
    assert query == None


@pytest.fixture
def takeout_json_data():
    """Base fixture for Google Takeout JSON data"""
    return {
        "header": "Google Suche",
        "title": "Some search",
        "titleUrl": "https://www.google.com/search?q=test",
        "time": "2025-01-09T14:39:34.364Z",
        "products": ["Google Suche"],
    }


@pytest.fixture
def valid_takeout_zip(takeout_json_data):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("Takeout/MyActivity.json", json.dumps([takeout_json_data]))
    zip_buffer.seek(0)
    return zipfile.ZipFile(zip_buffer)


@pytest.fixture
def invalid_zip():
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Add JSON with wrong structure
        invalid_data = [{"wrong": "structure"}]
        zf.writestr("some_file.json", json.dumps(invalid_data))
    zip_buffer.seek(0)
    return zipfile.ZipFile(zip_buffer)


@pytest.fixture
def multiple_json_zip(takeout_json_data):
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Add invalid JSON first
        invalid_data = [{"wrong": "structure"}]
        zf.writestr("invalid.json", json.dumps(invalid_data))

        # Add valid Google Takeout JSON
        zf.writestr("Takeout/MyActivity.json", json.dumps([takeout_json_data]))
    zip_buffer.seek(0)
    return zipfile.ZipFile(zip_buffer)


@pytest.fixture
def maps_data_zip():
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Sample of the Maps data
        maps_data = [
            {
                "header": "Maps",
                "title": "Heidelberg Castle",
                "titleUrl": "https://www.google.com/maps/place/Heidelberg+Castle/@49.4106196,8.7153092,16z/data=!3m1!4b1!4m2!3m1!1s0x4797c100ca43db93:0x6d672e3649e97eea",
                "time": "2025-01-08T09:34:22.644Z",
                "products": ["Maps"],
                "activityControls": ["Web & App Activity"],
            },
            {
                "header": "Maps",
                "title": "Searched for tea",
                "titleUrl": "https://www.google.com/maps/search/tea/@49.4091535,8.6775828,15z/data=!3m1!4b1",
                "time": "2025-01-08T09:31:59.365Z",
                "products": ["Maps"],
                "activityControls": ["Web & App Activity"],
                "locationInfos": [
                    {
                        "name": "At this general area",
                        "url": "https://www.google.com/maps/@?api=1&map_action=map&center=49.328947,8.752333&zoom=10",
                        "source": "Based on your past activity",
                    }
                ],
            },
        ]
        zf.writestr("Takeout/Maps/MyActivity.json", json.dumps(maps_data))
    zip_buffer.seek(0)
    return zipfile.ZipFile(zip_buffer)


@pytest.fixture
def search_product_zip():
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Add valid Google Takeout JSON with "Search" product
        valid_data = [
            {
                "header": "Search",
                "title": "Searched for heidelberg castle",
                "titleUrl": "https://www.google.com/search?q=heidelberg+castle",
                "time": "2025-01-08T09:34:46.043Z",
                "products": ["Search"],
                "activityControls": ["Web & App Activity"],
            }
        ]
        zf.writestr("Takeout/MyActivity.json", json.dumps(valid_data))
    zip_buffer.seek(0)
    return zipfile.ZipFile(zip_buffer)


def assert_google_search_export(data):
    """Helper function to verify Google Search export data structure"""
    assert isinstance(data, list)
    assert len(data) == 1
    assert all(
        key in data[0] for key in ["header", "title", "time", "products", "titleUrl"]
    )


def test_find_google_search_export_valid(valid_takeout_zip):
    from port.script import find_google_search_export

    data = find_google_search_export(valid_takeout_zip)
    assert_google_search_export(data)


def test_find_google_search_export_invalid(invalid_zip):
    from port.script import find_google_search_export, GoogleTakeoutNotFoundError

    with pytest.raises(GoogleTakeoutNotFoundError):
        find_google_search_export(invalid_zip)


def test_find_google_search_export_multiple(multiple_json_zip):
    from port.script import find_google_search_export

    data = find_google_search_export(multiple_json_zip)
    assert_google_search_export(data)


def test_find_google_search_export_empty_zip():
    from port.script import find_google_search_export, GoogleTakeoutNotFoundError

    empty_buffer = BytesIO()
    with zipfile.ZipFile(empty_buffer, "w") as zf:
        pass  # Create empty zip
    empty_buffer.seek(0)
    empty_zip = zipfile.ZipFile(empty_buffer)

    with pytest.raises(GoogleTakeoutNotFoundError):
        find_google_search_export(empty_zip)


def test_find_google_search_export_maps_data(maps_data_zip):
    from port.script import find_google_search_export, GoogleTakeoutNotFoundError

    with pytest.raises(GoogleTakeoutNotFoundError):
        find_google_search_export(maps_data_zip)


def test_find_google_search_export_search_product(search_product_zip):
    from port.script import find_google_search_export

    data = find_google_search_export(search_product_zip)
    assert_google_search_export(data)
    assert data[0]["products"] == ["Search"]


def test_extract_search_data_with_google_redirect_url():
    data = [
        {
            "header": "Google Suche",
            "title": "Habeck will kein TV-Duell mit Weidel ...",
            "titleUrl": "https://www.google.com/url?q=https://www.tagesschau.de/inland/bundestagswahl/tv-duell-habeck-absage-100.html&usg=AOvVaw14OeCEYWWJzc6cqvroGnXB",
            "time": "2025-02-18T07:49:17.826Z",  # Changed to February
            "products": ["Google Suche"],
        }
    ]
    from port.script import extract_search_data

    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 0
    assert len(clicks_df) == 1
    assert (
        clicks_df["Link"].iloc[0]
        == "https://www.tagesschau.de/inland/bundestagswahl/tv-duell-habeck-absage-100.html"
    )


def test_extract_search_data_index_enumeration():
    data = [
        {
            "header": "Google Suche",
            "title": "First search",
            "titleUrl": "https://www.google.com/search?q=first",
            "time": "2025-02-09T14:39:34.364Z",  # Changed to February
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "Second search",
            "titleUrl": "https://www.google.com/search?q=second",
            "time": "2025-02-09T14:39:35.364Z",  # Changed to February
            "products": ["Google Suche"],
        },
    ]
    searches_df, clicks_df = extract_search_data(data)
    assert list(searches_df["Nummer"]) == ["1", "2"]


def test_extract_search_data_date_range():
    data = [
        {
            "header": "Google Suche",
            "title": "Too early",
            "titleUrl": "https://www.google.com/search?q=early",
            "time": "2019-01-11T20:59:59.000Z",
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "In range",
            "titleUrl": "https://www.google.com/search?q=range",
            "time": "2025-01-12T01:00:00.000Z",
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "In range 2",
            "titleUrl": "https://www.google.com/search?q=range2",
            "time": "2025-03-02T11:59:59.000Z",
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "In range 3",
            "titleUrl": "https://www.google.com/search?q=late",
            "time": "2025-03-02T12:00:01.000Z",
            "products": ["Google Suche"],
        },
    ]
    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 3
    assert list(searches_df["Nummer"]) == ["1", "2", "3"]


def test_extract_search_data_url_titles():
    data = [
        {
            "header": "Google Suche",
            "title": "https://example.com/some/long/path",
            "titleUrl": "https://www.google.com/url?q=https://example.com/some/long/path",
            "time": "2025-02-09T14:39:34.364Z",
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "https://www.test-site.org/page",
            "titleUrl": "https://www.google.com/url?q=https://www.test-site.org/page",
            "time": "2025-02-09T14:39:42.587Z",
            "products": ["Google Suche"],
        },
        {
            "header": "Google Suche",
            "title": "Regular Title",
            "titleUrl": "https://example.net/page",
            "time": "2025-02-09T14:40:00.000Z",
            "products": ["Google Suche"],
        },
    ]
    searches_df, clicks_df = extract_search_data(data)

    assert len(clicks_df) == 3
    assert clicks_df["Suchergebnis"].tolist() == [
        "example.com",
        "test-site.org",
        "Regular Title",
    ]


def test_find_google_search_export_empty_google_takeout():
    """Test detection of Google Takeout archives that don't contain search data"""
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # Add the Google Takeout marker HTML file
        html_content = '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Google Data Export Archive Contents</title>'
        zf.writestr("index.html", html_content)
        # Add some other non-search data
        other_data = [{"header": "Maps", "products": ["Maps"]}]
        zf.writestr("Takeout/Maps/MyActivity.json", json.dumps(other_data))
    zip_buffer.seek(0)
    test_zip = zipfile.ZipFile(zip_buffer)

    with pytest.raises(NoGoogleSearchDataError):
        find_google_search_export(test_zip)


def test_extract_search_data_german_viewed_items():
    data = [
        {
            "header": "Search",
            "title": "Normal result angesehen",
            "titleUrl": "https://www.google.com/url?q=https://example.com",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
        {
            "header": "Search",
            "title": "Searched for cookies",
            "titleUrl": "https://www.google.com/search?q=cookies",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
    ]
    from port.script import extract_search_data

    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 1
    assert searches_df["Suchbegriff"].iloc[0] == "cookies"
    assert len(clicks_df) == 0


def test_extract_search_data_german_clicked_items():
    data = [
        {
            "header": "Search",
            "title": "Example Site aufgerufen",
            "titleUrl": "https://www.google.com/url?q=https://example.com",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
        {
            "header": "Search",
            "title": "Test Website aufgerufen",
            "titleUrl": "https://www.google.com/url?q=https://test.com",
            "time": "2025-02-08T09:35:03.562Z",
            "products": ["Search"],
        },
    ]
    from port.script import extract_search_data

    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 0
    assert len(clicks_df) == 2
    assert clicks_df["Suchergebnis"].iloc[0] == "Example Site"
    assert clicks_df["Link"].iloc[0] == "https://example.com"
    assert clicks_df["Suchergebnis"].iloc[1] == "Test Website"
    assert clicks_df["Link"].iloc[1] == "https://test.com"


def test_parse_google_search_json_valid():
    valid_json = json.dumps(
        [
            {
                "header": "Google Suche",
                "title": "Test search",
                "titleUrl": "https://www.google.com/search?q=test",
                "time": "2025-01-09T14:39:34.364Z",
                "products": ["Google Suche"],
            }
        ]
    )
    from io import StringIO

    data = parse_google_search_json(StringIO(valid_json))
    assert data is not None
    assert len(data) == 1
    assert data[0]["header"] == "Google Suche"
    assert data[0]["title"] == "Test search"


def test_parse_google_search_json_invalid():
    invalid_cases = [
        "[]",  # Empty array
        "[{}]",  # Missing required fields
        '{"not": "an array"}',  # Not an array
        "invalid json",  # Invalid JSON
    ]
    for invalid_json in invalid_cases:
        from io import StringIO

        data = parse_google_search_json(StringIO(invalid_json))
        assert data is None


def test_parse_google_search_html_valid_query():
    valid_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Google Account - Aktivitäten</title></head>
    <body>
        <div class="outer-cell">
            <div class="header-cell">
                <p>Google Suche<br /></p>
            </div>
            <div class="content-cell">
            Gesucht nach: <a
              href="https://www.google.com/search?q=dominosteine+rezept"
              >dominosteine rezept</a
            ><br />10.01.2025, 17:21:55 MEZ
          </div>
        </div>
    </body>
    </html>
    """
    data = parse_google_search_html(valid_html)
    assert data is not None
    assert len(data) == 1
    assert data[0]["header"] == "Google Suche"
    assert data[0]["title"] == "Gesucht nach: dominosteine rezept"
    assert data[0]["titleUrl"] == "https://www.google.com/search?q=dominosteine+rezept"
    assert data[0]["time"] == "2025-01-10T17:21:55+01:00"
    assert data[0]["products"] == ["Google Suche"]


def test_parse_google_search_html_valid_clicked():
    valid_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Google Account - Aktivitäten</title></head>
    <body>
        <div class="outer-cell">
            <div class="header-cell">
                <p>Google Suche<br /></p>
            </div>
            <div
            class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
          >
            <a
              href="https://www.google.com/url?q=https://www.koffer-direkt.de/products/vaude-city-duffel-65-reisetasche-70-cm-baltic-sea%3Fkendall_source%3Dgoogle%26kendall_campaign%3D21057346217%26kendall_adid%3D%26gad_source%3D1%26gbraid%3D%7Bgbraid%7D&amp;usg=AOvVaw27Xv9b_daTH0gg0SIyPqu5"
              >https://www.koffer-direkt.de/products/vaude-city-duffel-65-reisetasche-70-cm-baltic-sea?kendall_source=google&amp;kendall_campaign=21057346217&amp;kendall_adid=&amp;gad_source=1&amp;gbraid={gbraid}</a
            > aufgerufen<br />10.01.2025, 17:34:42 MEZ
          </div>
        </div>
    </body>
    </html>
    """
    data = parse_google_search_html(valid_html)
    assert data is not None
    assert len(data) == 1
    assert data[0]["header"] == "Google Suche"
    assert (
        data[0]["title"]
        == "https://www.koffer-direkt.de/products/vaude-city-duffel-65-reisetasche-70-cm-baltic-sea?kendall_source\u003dgoogle\u0026kendall_campaign\u003d21057346217\u0026kendall_adid\u003d\u0026gad_source\u003d1\u0026gbraid\u003d{gbraid} aufgerufen"
    )
    assert (
        data[0]["titleUrl"]
        == "https://www.google.com/url?q\u003dhttps://www.koffer-direkt.de/products/vaude-city-duffel-65-reisetasche-70-cm-baltic-sea%3Fkendall_source%3Dgoogle%26kendall_campaign%3D21057346217%26kendall_adid%3D%26gad_source%3D1%26gbraid%3D%7Bgbraid%7D\u0026usg\u003dAOvVaw27Xv9b_daTH0gg0SIyPqu5"
    )
    assert data[0]["time"] == "2025-01-10T17:34:42+01:00"
    assert data[0]["products"] == ["Google Suche"]


def test_parse_google_search_html_invalid():
    invalid_cases = [
        "<html></html>",  # Empty HTML
        "<html><head><title>Not Google</title></head></html>",  # Wrong title
        "<html><head><title>Google</title></head><body><div>No activity data</div></body></html>",  # Missing activity data
        "invalid html",  # Invalid HTML
    ]
    for invalid_html in invalid_cases:
        data = parse_google_search_html(invalid_html)
        assert data is None


def test_parses_json_and_html_in_the_same_way():
    html = """
    <html>
    <head>
        <title>Mein Aktivitätsverlauf</title>
    </head>
    <body>
        <div class="mdl-grid">
        <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
            <div class="mdl-grid">
            <div class="header-cell mdl-cell mdl-cell--12-col">
                <p class="mdl-typography--title">Google Suche<br /></p>
            </div>
            <div
                class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
            >
                Gesucht nach: <a
                href="https://www.google.com/search?q=takeout_20250113_mika_html"
                >takeout_20250113_mika_html</a
                ><br />13.01.2025, 08:38:09 MEZ
            </div>
            <div
                class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"
            ></div>
            <div
                class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption"
            >
                <b>Produkte:</b><br />&emsp;Google Suche<br /><b>Standorte:</b
                ><br />&emsp;<a
                href="https://www.google.com/maps/@?api=1&amp;map_action=map&amp;center=49.493809,8.465942&amp;zoom=12"
                >Ungefähre Gegend</a
                >
                - Von meinem Gerät<br /><b>Warum steht das hier?</b
                ><br />&emsp;Diese Aktivität wurde in Ihrem Google-Konto
                gespeichert, weil die folgenden Einstellungen aktiviert
                waren:&nbsp;Web- &amp; App-Aktivitäten.&nbsp;<a
                href="https://myaccount.google.com/activitycontrols"
                >Hier können Sie diese Einstellungen bearbeiten.</a
                >
            </div>
            </div>
        </div>
        </div>
    </body>
    </html>
    """

    json_str = """
    [{
        "header": "Google Suche",
        "title": "Gesucht nach: takeout_20250113_mika_html",
        "titleUrl": "https://www.google.com/search?q\u003dtakeout_20250113_mika_html",
        "time": "2025-01-13T07:38:09.000Z",
        "products": ["Google Suche"]
        }]
    """

    html_results = parse_google_search_html(html)
    json_results = parse_google_search_json(StringIO(json_str))
    assert len(html_results) == len(json_results)
    for html, json in zip(html_results, json_results):
        # Drop time since they are differently formatted
        html_time = html.pop("time")
        json_time = json.pop("time")
        assert html == json
        assert pd.to_datetime(html_time).tz_convert("UTC") == pd.to_datetime(
            json_time
        ).tz_convert("UTC")


def test_html_parser_uses_day_first_logic_for_dates():
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Google Account - Aktivitäten</title></head>
    <body>
        <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
        <div class="mdl-grid">
          <div class="header-cell mdl-cell mdl-cell--12-col">
            <p class="mdl-typography--title">Google Suche<br /></p>
          </div>
          <div
            class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
          >
            <a
              href="https://www.google.com/url?q=https://www.dwd.de/DE/service/lexikon/begriffe/S/Schnee.html&amp;usg=AOvVaw2A5Q4460CYt_dkhMWGX90F"
              >Wetter und Klima - Deutscher Wetterdienst - Glossar - Schnee</a
            > aufgerufen<br />09.01.2025, 15:39:46 MEZ
          </div>
    """
    data = parse_google_search_html(html)
    assert data[0]["time"] == "2025-01-09T15:39:46+01:00"


def test_dont_include_google_in_results():
    data = [
        {
            "header": "Google Suche",
            "title": "Some search",
            "titleUrl": "https://www.google.com",
            "time": "2025-02-09T14:39:34.364Z",
            "products": ["Google Suche"],
        }
    ]
    searches_df, clicks_df = extract_search_data(data)
    assert len(searches_df) == 0
    assert len(clicks_df) == 0


def test_html_ignores_non_search_data():
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr(
            "Search/MyActivity.html",
            """
            <!DOCTYPE html>
            <html>
            <head><title>Google Account - Aktivitäten</title></head>
            <body>
                <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
                <div class="mdl-grid">
                <div class="header-cell mdl-cell mdl-cell--12-col">
                    <p class="mdl-typography--title">Image Search<br /></p>
                </div>
                <div
                    class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
                >
                    <a
                    href="https://www.google.com/url?q=https://www.dwd.de/DE/service/lexikon/begriffe/S/Schnee.html&amp;usg=AOvVaw2A5Q4460CYt_dkhMWGX90F"
                    >Wetter und Klima - Deutscher Wetterdienst - Glossar - Schnee</a
                    > aufgerufen<br />09.01.2025, 15:39:46 MEZ
                </div>
            """.encode(
                "utf8"
            ),
        )
        with pytest.raises(NoGoogleSearchDataError):
            find_google_search_export(zf)


def test_html_detects_search_data():
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr(
            "Search/MyActivity.html",
            """
            <!DOCTYPE html>
            <html>
            <head><title>Google Account - Aktivitäten</title></head>
            <body>
                <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
                <div class="mdl-grid">
                <div class="header-cell mdl-cell mdl-cell--12-col">
                    <p class="mdl-typography--title">Search<br /></p>
                </div>
                <div
                    class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1"
                >
                    <a
                    href="https://www.google.com/url?q=https://www.dwd.de/DE/service/lexikon/begriffe/S/Schnee.html&amp;usg=AOvVaw2A5Q4460CYt_dkhMWGX90F"
                    >Wetter und Klima - Deutscher Wetterdienst - Glossar - Schnee</a
                    > aufgerufen<br />09.01.2025, 15:39:46 MEZ
                </div>
            """.encode(
                "utf8"
            ),
        )
        assert find_google_search_export(zf) is not None
