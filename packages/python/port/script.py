import port.api.props as props
from port.api.assets import *
from port.api.commands import CommandSystemDonate, CommandSystemExit, CommandUIRender

import dateutil.parser
import pandas as pd
import zipfile
import json
import re # new

from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
import dateutil.tz
import io

import logging


# A logging handler that logs all messages to an data frame.
# The first column is the log level, the second column is the message.
class DataFrameHandler(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self._data = []

    def emit(self, record):
        self._data.append({"Level": record.levelname, "Message": record.getMessage(), "Time": record.created})

    @property
    def df(self):
        return pd.DataFrame(self._data)


# Set up in logging using the dataframe handler
log_handler = DataFrameHandler()
logging.basicConfig(handlers=[log_handler], level=logging.INFO)
logger = logging.getLogger(__name__)


german_tz = dateutil.tz.gettz("Europe/Berlin")

# Include data from -365 days until current date
# Use timezone-aware timestamps for comparison
now = pd.Timestamp.now(tz="Europe/Berlin")
start_dt = now - pd.Timedelta(days=365)
end_dt = now + pd.Timedelta(days=1)  # include today


def process(sessionId):
    key = "google-search-history"
    logger.info(f"{key}: start")

    # STEP 1: select the file
    data = None
    while True:
        logger.info(f"{key}: prompt file")
        promptFile = prompt_file("application/zip")
        fileResult = yield render_donation_page(promptFile)

        if fileResult.__type__ == "PayloadString":
            logger.info(f"{key}: searching for Google Takeout data")
            try:
                # Try to find and extract Google Takeout data
                zipfile_ref = get_zipfile(fileResult.value)

                if zipfile_ref == "invalid":
                    raise zipfile.BadZipFile("Invalid zip file")

                # Find and parse the Google Search export
                json_data = find_google_search_export(zipfile_ref)
                logger.info(f"{key}: found valid Google Takeout data")

                # Extract search history and clicks into dataframes
                logger.info(f"{key}: extracting search history")
                data = extract_search_data(json_data)
                break

            except NoGoogleSearchDataError:
                value = json.dumps('{"status" : "no-search-data"}')
                yield donate(f"{sessionId}-{key}", value)
                yield render_no_search_data_page()
                return

            except GoogleTakeoutNotFoundError:
                logger.info(f"{key}: no valid Google Takeout data found")

                retry_result = yield render_donation_page(retry_confirmation())
                if retry_result.__type__ == "PayloadTrue":
                    continue
                else:
                    value = json.dumps('{"status" : "no-google-takeout-found"}')
                    yield donate(f"{sessionId}-{key}", value)
                    return
            except (zipfile.BadZipFile, IOError) as e:
                logger.info(f"{key}: error processing file - {str(e)}")
                retry_result = yield render_donation_page(retry_confirmation())
                if retry_result.__type__ == "PayloadTrue":
                    continue
                else:
                    logger.info(f"{key}: user cancelled")
                    break

    # STEP 2: ask for consent
    if data is not None:
        logger.info(f"{key}: prompt consent")
        prompt = prompt_consent(data, log_handler.df)
        consent_result = yield render_donation_page(prompt)
        if consent_result.__type__ == "PayloadJSON":
            logger.info(f"{key}: donate consent data")
            yield donate(f"{sessionId}-{key}", consent_result.value)
            yield donate(f"{sessionId}-{key}-log", log_handler.df.to_json(orient="records"))
        if consent_result.__type__ == "PayloadFalse":
            value = json.dumps(
                {"status": "donation declined", "log": log_handler.df.to_dict()}
            )
            yield donate(f"{sessionId}-{key}", value)


def render_donation_page(body):
    header = props.PropsUIHeader(
        props.Translatable(
            {
                "en": "Google Search History",
                "de": "Google-Suchverlauf",
                "nl": "Google Zoekgeschiedenis",
            }
        )
    )

    page = props.PropsUIPageDataSubmission("Zip", header, body)
    return CommandUIRender(page)


def render_no_search_data_page():
    header = props.PropsUIHeader(
        props.Translatable(
            {
                "en": "No Google Search Data",
                "de": "Keine Google-Suchdaten",
                "nl": "Geen Google Zoekgegevens",
            }
        )
    )

    body = props.PropsUIPromptConfirm(
        text=props.Translatable(
            {
                "en": "Your data package does not contain any Google search data, either because you did not request it during data export, or your privacy settings at Google are set this way. By clicking End, you can complete your study participation.",
                "de": "Ihr Datenpaket enthält keine Google Suchdaten, da Sie diese entweder beim Datenexport nicht angefordert haben, oder Ihre Privatsphäre-Einstellungen bei Google dies so festlegen. Mit Klicken auf 'Beenden' gelangen Sie zurück zur Übersicht. Wenn Sie sich nicht sicher sind, dass Sie ausschließlich 'Meine Aktivitären' bei Google angefordert haben, können Sie erneut mit Schritt '(1) Daten anfordern & herunterladen' beginnen. Andernfalls können Sie die Kurzbefragung starten – wir würden uns sehr über Ihre Teilnahme freuen!",
                "nl": "Uw datapakket bevat geen Google-zoekgegevens, omdat u deze niet hebt aangevraagd tijdens de gegevensexport, of omdat uw privacy-instellingen bij Google zo zijn ingesteld. Door op Beëindigen te klikken, kunt u uw deelname aan de studie voltooien.",
            }
        ),
        ok=props.Translatable(
            {
                "en": "End",
                "de": "Beenden",
                "nl": "Beëindigen",
            }
        ),
    )
    page = props.PropsUIPageDataSubmission("NoGoogleSearchData", header, body)
    return CommandUIRender(page)


def retry_confirmation():
    text = props.Translatable(
        {
            "en": "Unfortunately, we cannot process your file. Continue, if you are sure that you selected the right file. Try again to select a different file.",
            "de": "Es sieht so aus, als hätten Sie eine falsche Datei ausgewählt. Bitte überprüfen Sie, ob Sie die originale ZIP-Datei Ihres Google-Suchverlauf geladen haben, und klicken Sie anschließend auf 'Erneut veruchen', um die korrekte Datei hochzuladen. Mit einem Klick auf 'Beenden' gelangen Sie zurück zur Übersicht. Dort können Sie die Kurzbefragung starten – wir würden uns sehr über Ihre Teilnahme freuen!",
            "nl": "Helaas, kunnen we uw bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen.",
        }
    )
    ok = props.Translatable(
        {
            "en": "Try again",
            "de": "Erneut versuchen",
            "nl": "Probeer opnieuw",
        }
    )
    cancel = props.Translatable({"en": "Continue", "de": "Beenden", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file(extensions):
    description = props.Translatable(
        {
            "en": "Please select the zip file that you downloaded with your Google search history.",
            "de": "Wählen Sie bitte die heruntergeladene ZIP Datei aus mit Ihren Google-Suchverlauf.",
            "nl": "Selecteer een willekeurige zip file die u heeft opgeslagen op uw apparaat.",
        }
    )

    return props.PropsUIPromptFileInput(description, extensions)


def prompt_extraction_message(message, percentage):
    description = props.Translatable(
        {
            "en": "One moment please. Information is now being extracted from the selected file.",
            "de": "Einen Moment bitte. Es werden nun Informationen aus der ausgewählten Datei extrahiert.",
            "nl": "Een moment geduld. Informatie wordt op dit moment uit het geselecteerde bestaand gehaald.",
        }
    )

    return props.PropsUIPromptProgress(description, message, percentage)


def get_zipfile(filename):
    try:
        return zipfile.ZipFile(filename)
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        return "invalid"


def get_files(zipfile_ref):
    try:
        return zipfile_ref.namelist()
    except (zipfile.BadZipFile, AttributeError) as e:
        return []


def extract_file(zipfile_ref, filename):
    try:
        # make it slow for demo reasons only
        info = zipfile_ref.getinfo(filename)
        return (filename, info.compress_size, info.file_size)
    except (zipfile.BadZipFile, KeyError) as e:
        return "invalid"


def prompt_consent(data, meta_data):
    search_title = props.Translatable(
        {
            "en": "Search terms",
            "de": "Suchbegriffe",
            "nl": "Zoektermen",
        }
    )

    clicks_title = props.Translatable(
        {
            "en": "Clicked search results",
            "de": "Angeklickte Suchergebnisse",
            "nl": "Aangeklikte zoekresultaten",
        }
    )

    log_title = props.Translatable(
        {"en": "Log messages", "de": "Log Nachrichten", "nl": "Log berichten"}
    )

    blocks = []
    if isinstance(data, tuple) and len(data) == 2:
        searches_df, clicks_df = data
        blocks = [
            props.PropsUIPromptConsentFormTable("searches", 1, search_title, props.Translatable({}), searches_df),
            props.PropsUIPromptConsentFormTable("clicks", 2, clicks_title, props.Translatable({}), clicks_df),
        ]

    meta_table = props.PropsUIPromptConsentFormTable(
        "log_messages", 3, log_title, props.Translatable({}), meta_data
    )
    blocks.append(
        props.PropsUIDataSubmissionButtons(
            donate_question=props.Translatable({"en": "Do you want to donate your data?", "de": "Möchten Sie Ihre Daten spenden?", "nl": "Wilt u uw gegevens doneren?"}),
            donate_button=props.Translatable({"en": "Donate", "de": "Spenden", "nl": "Doneren"})
        )
    )
    return blocks


def donate(key, json_string):
    return CommandSystemDonate(key, json_string)


def exit(code, info):
    return CommandSystemExit(code, info)


def normalize_date_string(date_string):

    # adjust Cyrillic date
    if "г." in date_string or re.search(r"[а-яА-ЯёЁ]", date_string):
        date_string = re.sub(r"\b([а-яА-ЯёЁ]{3,4})\.", r"\1", date_string)  # remove . after kyrillisch onth
        date_string = re.sub(r"[\u202F\s]?г\.,?", "", date_string)  # remove r. (russian year)

    # map month names
    month_map = {
        # Russian
        "янв": "Jan", "фев": "Feb", "мар": "Mar", "апр": "Apr", "май": "May",
        "июн": "Jun", "июл": "Jul", "авг": "Aug", "сен": "Sep",
        "окт": "Oct", "ноя": "Nov", "дек": "Dec",

        # Turkish
        "oca": "Jan", "şub": "Feb", "mar": "Mar", "nis": "Apr", "mayıs": "May",
        "haz": "Jun", "tem": "Jul", "ağu": "Aug", "eyl": "Sep",
        "eki": "Oct", "kas": "Nov", "ara": "Dec",

        # Spanish
        "ene": "Jan", "feb": "Feb", "mar": "Mar", "abr": "Apr", "may": "May",
        "jun": "Jun", "jul": "Jul", "ago": "Aug", "sep": "Sep",
        "oct": "Oct", "nov": "Nov", "dic": "Dec",

        # Italian
        "gen": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr", "mag": "May",
        "giu": "Jun", "lug": "Jul", "ago": "Aug", "set": "Sep",
        "ott": "Oct", "nov": "Nov", "dic": "Dec",

        # French
        "janv": "Jan", "févr": "Feb", "mars": "Mar", "avr": "Apr", "mai": "May",
        "juin": "Jun", "juil": "Jul", "août": "Aug", "sept": "Sep",
        "oct": "Oct", "nov": "Nov", "déc": "Dec",
        "févr.": "Feb", "janv.": "Jan", "déc.": "Dec",

        # Polish
        "sty": "Jan", "lut": "Feb", "mar": "Mar", "kwi": "Apr", "maj": "May",
        "cze": "Jun", "lip": "Jul", "sie": "Aug", "wrz": "Sep",
        "paź": "Oct", "lis": "Nov", "gru": "Dec",
    }

    # replace month name, case-insensitiv
    for local, eng in month_map.items():
        date_string = re.sub(rf"\b{local}\b", eng, date_string, flags=re.IGNORECASE)

    return date_string


def is_google_search_url(url):
    """
    Determine if a URL is a Google search URL.
    Accepts any Google TLD (e.g., google.com, google.de, google.nl)

    Args:
        url: URL string to check

    Returns:
        tuple: (is_search, query) where is_search is boolean and query is the search term or None
    """
    try:
        if not url:  # Handle None or empty string
            return False, None

        parsed_url = urlparse(url)
        if not parsed_url.netloc:  # Handle invalid URLs
            return False, None

        # Check if domain is a Google domain (e.g., www.google.com, www.google.de)
        if parsed_url.netloc.startswith("www.google.") and parsed_url.path == "/search":
            query_params = parse_qs(parsed_url.query)
            if "q" in query_params:
                return True, query_params["q"][0]
    except (ValueError, AttributeError, TypeError) as e:
        pass
    return False, None


def extract_search_data(data):
    if not isinstance(data, list):
        return pd.DataFrame(columns=["Datum", "Uhrzeit", "Nummer", "Suchbegriff"]), pd.DataFrame(
            columns=["Datum", "Uhrzeit", "Nummer", "Suchergebnis", "Link"]
        )

    searches = []
    clicks = []

    def resolve_google_redirect(url):
        parsed = urlparse(url)
        if parsed.netloc.startswith("www.google.") and parsed.path == "/url":
            query = parse_qs(parsed.query)
            if "q" in query:
                return query["q"][0]
        return url

    def format_title(title):
        try:
            parsed = urlparse(title)
            # Check if it looks like a URL - must have netloc (domain) and valid scheme
            if parsed.netloc and parsed.scheme in ("http", "https"):
                # Remove 'www.' prefix if it exists
                return parsed.netloc.replace("www.", "", 1)
            return title
        except (ValueError, AttributeError) as e:
            return title

    def is_google_homepage(url):
        parsed = urlparse(url)
        return parsed.netloc.startswith("www.google.") and parsed.path in ["/", ""]

    records = []
    for item in data:
        if not all(
            key in item for key in ["header", "title", "titleUrl", "time", "products"]
        ):
            continue

        try:
            timestamp = pd.to_datetime(item["time"])

            # Ensure consistent timezone comparison
            if timestamp.tz is None:
                timestamp = timestamp.tz_localize("UTC")
            if timestamp.tz.zone != "CET":
                timestamp = timestamp.tz_convert("CET")

            # Strictly enforce the date range
            if timestamp < start_dt or timestamp > end_dt:
                continue

            title = item["title"]
            if title.startswith("Visited "):
                item["title"] = title[len("Visited ") :]
            elif title.endswith(" aufgerufen"):
                item["title"] = title[: -len(" aufgerufen")]
            elif title.startswith("Has visitado "):
                item["title"] = title[len("Has visitado ") :]
            elif title.startswith("Посещена страница "):
                item["title"] = title[len("Посещена страница ") :]
            elif title.startswith("Hai visitato "):
                item["title"] = title[len("Hai visitato ") :]
            elif title.startswith("Vous avez consulté "):
                item["title"] = title[len("Vous avez consulté ") :]
            elif title.endswith(" sayfasını ziyaret ettiniz"):
                item["title"] = title[: -len(" sayfasını ziyaret ettiniz")]
            elif title.startswith("Odwiedzono: "):
                item["title"] = title[len("Odwiedzono: ") :]
            elif title.startswith("Viewed ") or title.endswith(" angesehen") or title.startswith("Has visto ") or title.startswith("Просмотрено: ") or title.startswith("Hai visualizzato ") or title.startswith("Şunu görüntülediniz: ") or title.startswith("Obejrzano: "):
                continue  # Skip Viewed items entirely

            if is_google_homepage(item["titleUrl"]):
                continue  # Skip Google homepage visits

            records.append(item)
        except (ValueError, AttributeError) as e:
            continue

    for i, item in enumerate(records, start=1):
        timestamp = pd.to_datetime(item["time"])
        date = timestamp.strftime("%d-%m-%Y")
        hour = timestamp.strftime("%H:00")
        index = str(i)

        final_url = resolve_google_redirect(item["titleUrl"])
        is_search, query = is_google_search_url(final_url)
        if is_search:
            searches.append({"Datum": date, "Uhrzeit": hour, "Nummer": index, "Suchbegriff": query})
        else:
            clicks.append(
                {
                    "Datum": date,
                    "Uhrzeit": hour,
                    "Nummer": index,
                    "Suchergebnis": format_title(item["title"]),
                    "Link": final_url,
                }
            )

    searches_df = pd.DataFrame(searches)
    clicks_df = pd.DataFrame(clicks)

    # Ensure columns exist even if dataframes are empty
    searches_df = searches_df.reindex(columns=["Datum", "Uhrzeit", "Nummer", "Suchbegriff"])
    clicks_df = clicks_df.reindex(columns=["Datum", "Uhrzeit", "Nummer", "Suchergebnis", "Link"])

    return searches_df, clicks_df


class GoogleTakeoutNotFoundError(Exception):
    """Raised when no valid Google Takeout data is found in the zip file"""

    pass


class NoGoogleSearchDataError(Exception):
    """Raised when a Google Takeout archive is found but contains no search data"""

    pass


def parse_google_search_json(file_obj):
    """
    Parse Google Search data from a JSON file.

    Args:
        file_obj: File object containing JSON data

    Returns:
        list: The parsed activity data if valid search data is found
        None: If no valid search data is found
    """
    try:
        data = json.load(file_obj)
    except json.JSONDecodeError:
        return None

    try:
        if isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            if all(
                key in first_item
                for key in ["header", "title", "time", "products"]
            ) and any(
                key in first_item
                for key in ["titleUrl", "subtitles"]
            ) and any(
                product in ["Search", "Google Suche", "Búsqueda", "Recherche", "Поиск", "Ricerca", "Arama", "Wyszukiwarka"]
                for product in first_item["products"]
            ):
                return data
    except (KeyError, AttributeError, TypeError):
        pass
    return None


def parse_google_search_html(html_content):
    """
    Parse Google Search data from an HTML file.

    Args:
        html_content: String containing HTML content

    Returns:
        list: The parsed activity data if valid search data is found
        None: If no valid search data is found
    """
    soup = BeautifulSoup(html_content, "html.parser")

    activities = []

    # Find all activity entries
    for cell in soup.find_all("div", class_="outer-cell"):
        try:
            # Get header (activity type)
            try:
                header_elem = cell.select(".header-cell")[0]
            except IndexError:
                continue

            header = header_elem.get_text(strip=True)
            if header not in ["Google Suche", "Search", "Búsqueda", "Recherche", "Поиск", "Ricerca", "Arama", "Wyszukiwarka"]:
                continue

            # Get content
            try:
                content = cell.select(".content-cell")[0]
            except IndexError:
                continue

            # Extract URL before removing the link
            link = content.find("a")
            title_url = ""
            if link and "href" in link.attrs:
                title_url = link["href"]

            parts = list(content.stripped_strings)

            if len(parts) == 3:
                title = " ".join(parts[:-1])

                date_string = normalize_date_string(parts[-1])

                try:
                    timestamp = dateutil.parser.parse(
                        date_string,
                        tzinfos={
                            "MEZ": german_tz,
                        },
                        dayfirst=True,
                    )
                except dateutil.parser.ParserError:
                    continue

                activity = {
                    "header": header,
                    "title": title,
                    "titleUrl": title_url,
                    "time": timestamp.isoformat(),
                    "products": ["Google Suche"],
                }
                activities.append(activity)

        except (AttributeError, IndexError, KeyError) as e:
            continue

    return activities if activities else None


def find_google_search_export(zipfile_ref):
    """
    Find and validate Google Search export files in a zip archive.
    Supports both JSON and HTML formats from Google Takeout.

    Args:
        zipfile_ref: ZipFile object to search through

    Returns:
        list: The parsed activity data if found

    Raises:
        GoogleTakeoutNotFoundError: If no valid Google Takeout data is found
        NoGoogleSearchDataError: If valid Google Takeout is found but contains no search data
    """

    logger.info("Searching for Google Search data in zip file")

    # First try JSON files
    json_files = [f for f in zipfile_ref.namelist() if f.lower().endswith(".json")]

    if not json_files:
        logger.info("No JSON files found in archive.")
        
    for f in json_files:
        try:
            with zipfile_ref.open(f) as file:
                parsed_data = parse_google_search_json(file)
                if parsed_data:
                    logger.info(f"Found search data in JSON file: {f}")
                    return parsed_data
        except Exception as e:
            logger.warning(f"Error parsing JSON file {f}: {e}")
            continue

    
    # Try HTML files
    html_files = [f for f in zipfile_ref.namelist() if f.lower().endswith(".html")]
    
    if not html_files:
        logger.info("No HTML files found in archive.")
    
    for f in html_files:
        try:
            with zipfile_ref.open(f) as file:
                html_content = file.read().decode("utf-8")
                parsed_data = parse_google_search_html(html_content)
                if parsed_data:
                    logger.info(f"Found search data in HTML file: {f}")
                    return parsed_data
        except Exception as e:
            logger.warning(f"Error parsing HTML file {f}: {e}")
            continue
            

    # Check if this is a Google Takeout archive
    all_filenames = zipfile_ref.namelist()
    has_takeout = any("takeout" in f.lower() for f in all_filenames)

    if has_takeout:
        logger.error("Takeout folder found, but no search data detected.")
        raise NoGoogleSearchDataError()
    else:
        logger.error("No indicators of a Google Takeout folder found.")
        raise GoogleTakeoutNotFoundError("No valid Google Takeout data found in zip file")
