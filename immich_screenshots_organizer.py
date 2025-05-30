import requests
import argparse
import logging
import sys
import datetime
from collections import defaultdict


parser = argparse.ArgumentParser(
    description="Create Immich Albums from an external library path based on the top level folders",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("album_name", help="The album name for your screenshots")
parser.add_argument("api_url", help="The root API URL of immich, e.g. https://immich.mydomain.com/api/")
parser.add_argument("api_key", help="The Immich API Key to use")
parser.add_argument("--include-low-res", default=False, action="store_true",
                    help="Include photos that are low resolution(under 1400x1400/~2MP). Default is false")
parser.add_argument("--with-smartsearch", default=False, action="store_true",
                    help="Include screenshots that are found using smart search. Default is false")
parser.add_argument("--archive-screens", default=False, action="store_true",
                    help="Archives all the screenshots to hide them from the timeline. Default is false")
parser.add_argument("-n", "--library-name",
                    help="The name of the library to look for screenshots in, if empty all libraries will be searched.")
parser.add_argument("-p", "--import-path",
                    help="The import path of the library to look for screenshots in, if left empty all libraries will be searched")
parser.add_argument("-u", "--unattended", action="store_true",
                    help="Do not ask for user confirmation after identifying albums. Set this flag to run script as a cronjob.")
parser.add_argument("-c", "--chunk-size", default=2000, type=int,
                    help="Maximum number of assets to add to an album with a single API call")
parser.add_argument("-C", "--fetch-chunk-size", default=5000, type=int,
                    help="Maximum number of assets to fetch with a single API call")
parser.add_argument("-l", "--log-level", default="INFO", choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'],
                    help="Log level to use")
args = vars(parser.parse_args())

# set up logger to log in logfmt format
logging.basicConfig(level=args["log_level"], stream=sys.stdout,
                    format='time=%(asctime)s level=%(levelname)s msg=%(message)s')
logging.Formatter.formatTime = (lambda self, record, datefmt=None: datetime.datetime.fromtimestamp(record.created,
                    datetime.timezone.utc).astimezone().isoformat(sep="T", timespec="milliseconds"))

album_name = args["album_name"]
root_url = args["api_url"]
api_key = args["api_key"]
low_res = args['include_low_res']
smartsearch = args['with_smartsearch']
archive_screens = args['archive_screens']
library_name = args["library_name"]
import_path = args["import_path"]
number_of_images_per_request = args["chunk_size"]
number_of_assets_to_fetch_per_request = args["fetch_chunk_size"]
unattended = args["unattended"]

logging.debug("album_name = %s", album_name)
logging.debug("root_url = %s", root_url)
logging.debug("api_key = %s", api_key)
logging.debug("low_res = %s", low_res)
logging.debug("smartsearch = %s", smartsearch)
logging.debug("archive_screens = %s", archive_screens)
logging.debug("library_name = %s", library_name)
logging.debug("library_name = %s", import_path)
logging.debug("number_of_images_per_request = %d", number_of_images_per_request)
logging.debug("number_of_assets_to_fetch_per_request = %d", number_of_assets_to_fetch_per_request)
logging.debug("unattended = %s", unattended)

# Request arguments for API calls
requests_kwargs = {
    'headers': {
        'x-api-key': api_key,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
}


# Yield successive n-sized
# chunks from l.
def divide_chunks(l, n):
    # looping till length l
    for i in range(0, len(l), n):
        yield l[i:i + n]


def getLibraryByPath(import_path):
    libraries = fetch_libraries()
    for library in libraries:
        if import_path in library['importPaths']:
            return library['id']
    return None


def getLibraryByName(lib_name):
    libraries = fetch_libraries()
    for library in libraries:
        if library['name'] == lib_name:
            return library['id']
    return None


def fetch_libraries():
    apiEndpoint = 'libraries'
    r = requests.get(root_url + apiEndpoint, **requests_kwargs)
    r.raise_for_status()
    return r.json()


# Fetches assets from the Immich API
# Takes different API versions into account for compatibility
def fetchServerVersion():
    # This API call was only introduced with version 1.106.1, so it will fail
    # for older versions.
    # Initialize the version with the latest version without this API call
    version = {'major': 1, 'minor': 105, "patch": 1}

    try:
        # 1.118.x and up
        r = requests.get(root_url + 'server/version', **requests_kwargs)
        if r.status_code == 200:
            version = r.json()
            logging.info("Detected Immich server version (new API) %s.%s.%s", version['major'], version['minor'], version['patch'])
            return version
    except requests.exceptions.RequestException as e:
        logging.warning("Failed to call new API endpoint: %s", e)

    try:
        # 1.106.x - 1.117.x
        r = requests.get(root_url + 'server-info/version', **requests_kwargs)
        if r.status_code == 200:
            version = r.json()
            logging.info("Detected Immich server version (old API) %s.%s.%s", version['major'], version['minor'], version['patch'])
            return version
    except requests.exceptions.RequestException as e:
        logging.warning("Failed to call fallback API endpoint: %s", e)

    logging.info("Immich API not found or unsupported (version %s.%s.%s or older)", version['major'], version['minor'], version['patch'])
    return version


# Unused
def fetchAssetInfo(id):
    body = {}
    r = requests.get(root_url + 'assets/' + id, json=body, **requests_kwargs)
    r.raise_for_status()
    return r.json()


# Fetches assets from the Immich API
# Uses the /search/metadata call.
def fetchAssets():
    assets = []
    # prepare request body
    body = {}
    body['isOffline'] = 'false'
    body['type'] = 'IMAGE'
    body['withExif'] = True
    if (library_name is not None or import_path is not None) and library_id is not None:
        body['libraryId'] = library_id
        logging.debug("library_id: %s", library_id)
    # This API call allows a maximum page size of 1000
    number_of_assets_to_fetch_per_request_search = min(1000, number_of_assets_to_fetch_per_request)
    body['size'] = number_of_assets_to_fetch_per_request_search
    # Initial API call, let's fetch our first chunk
    page = 1
    body['page'] = str(page)
    r = requests.post(root_url + 'search/metadata', json=body, **requests_kwargs)
    r.raise_for_status()
    responseJson = r.json()
    assetsReceived = responseJson['assets']['items']
    logging.debug("Received %s assets with chunk %s", len(assetsReceived), page)

    assets = assets + assetsReceived
    # If we got a full chunk size back, let's perfrom subsequent calls until we get less than a full chunk size
    while len(assetsReceived) == number_of_assets_to_fetch_per_request_search:
        page += 1
        body['page'] = page
        r = requests.post(root_url + 'search/metadata', json=body, **requests_kwargs)
        assert r.status_code == 200
        responseJson = r.json()
        assetsReceived = responseJson['assets']['items']
        logging.debug("Received %s assets with chunk %s", len(assetsReceived), page)
        assets += assetsReceived
    if smartsearch:
        assets += fetchAssetsSearchSmart()
    return assets

# Fetches assets from the Immich API
# Uses the /search/smart call.
def fetchAssetsSearchSmart():
    assets = []
    # prepare request body
    body = {}
    body['query'] = 'screenshot'
    body['isOffline'] = 'false'
    body['type'] = 'IMAGE'
    body['withExif'] = True
    if (library_name is not None or import_path is not None) and library_id is not None:
        body['libraryId'] = library_id
        logging.debug("library_id: %s", library_id)
    # This API call allows a maximum page size of 1000
    number_of_assets_to_fetch_per_request_search = min(1000, number_of_assets_to_fetch_per_request)
    body['size'] = number_of_assets_to_fetch_per_request_search
    # Initial API call, let's fetch our first chunk
    page = 1
    body['page'] = str(page)
    r = requests.post(root_url + 'search/smart', json=body, **requests_kwargs)
    r.raise_for_status()
    responseJson = r.json()
    assetsReceived = responseJson['assets']['items']
    logging.debug("Received %s assets with chunk %s", len(assetsReceived), page)

    assets = assets + assetsReceived
    # If we got a full chunk size back, let's perfrom subsequent calls until we get less than a full chunk size
    while len(assetsReceived) == number_of_assets_to_fetch_per_request_search:
        page += 1
        body['page'] = page
        r = requests.post(root_url + 'search/metadata', json=body, **requests_kwargs)
        assert r.status_code == 200
        responseJson = r.json()
        assetsReceived = responseJson['assets']['items']
        logging.debug("Received %s assets with chunk %s", len(assetsReceived), page)
        assets += assetsReceived
    return assets

# Fetches assets from the Immich API
def fetchAlbums():
    apiEndpoint = 'albums'
    r = requests.get(root_url + apiEndpoint, **requests_kwargs)
    r.raise_for_status()
    return r.json()


# Creates an album with the provided name and returns the ID of the
# created album
def createAlbum(albumName):
    apiEndpoint = 'albums'
    data = {
        'albumName': albumName,
        'description': albumName
    }
    r = requests.post(root_url + apiEndpoint, json=data, **requests_kwargs)
    assert r.status_code in [200, 201]
    return r.json()['id']


def unarchiveAsset(id):
    archiveAsset(id, True)


def archiveAsset(id, unarchiveAsset = False):
    apiEndpoint = 'assets/'
    data = {
        'isArchived': not unarchiveAsset,
    }
    r = requests.put(root_url + apiEndpoint + id, json=data, **requests_kwargs)
    assert r.status_code in [200, 201]
    return r.json()['isArchived']


# Adds the provided assetIds to the provided albumId
def addAssetsToAlbum(albumId, assets):
    apiEndpoint = 'albums'
    # Divide our assets into chunks of number_of_images_per_request,
    # So the API can cope
    assets_chunked = list(divide_chunks(assets, number_of_images_per_request))
    for assets_chunk in assets_chunked:
        data = {'ids': assets_chunk}
        r = requests.put(root_url + apiEndpoint + f'/{albumId}/assets', json=data, **requests_kwargs)
        if r.status_code not in [200, 201]:
            continue
        assert r.status_code in [200, 201]
        response = r.json()

        cpt = 0
        for res in response:
            if not res['success']:
                if res['error'] != 'duplicate':
                    logging.warning("Error adding an asset to an album: %s", res['error'])
            else:
                cpt += 1
        if cpt > 0:
            logging.info("%d new assets added to %s", cpt, album)


# append trailing slash to root URL
if root_url[-1] != '/':
    if root_url[-3:] != 'api':
        root_url += '/api/'
    else:
        root_url += '/'
elif root_url[-4:] != 'api/':
    root_url += 'api/'

serverVersion = fetchServerVersion()
if serverVersion['major'] == 1 and serverVersion['minor'] <= 105:
    logging.error("This code only works for version >1.105")
    exit(1)

library_id = None
if library_name is not None:
    library_id = getLibraryByName(library_name)
elif import_path is not None:
    library_id = getLibraryByPath(import_path)

logging.info("Requesting all assets")
assets = fetchAssets()
logging.info("%d photos found", len(assets))

logging.info("Sorting assets that are screenshots to the album...")
album_to_assets = defaultdict(list)
assets_to_archive = []
assets_to_unarchive = []

if not len(album_name) > 0:
    logging.warning("Got empty album name for screenshots, exiting...")
    exit(1)

for asset in assets:
    if "exposureTime" in asset['exifInfo'] and asset['exifInfo']['exposureTime'] is None:
        album_to_assets[album_name].append(asset['id'])
        if archive_screens:
            assets_to_archive.append(asset['id'])
    elif low_res and (int(asset['exifInfo']['exifImageWidth']) * int(asset['exifInfo']['exifImageHeight'])) < 1960000:
        album_to_assets[album_name].append(asset['id'])
        if archive_screens:
            assets_to_archive.append(asset['id'])

album_to_assets = {k: v for k, v in sorted(album_to_assets.items(), key=(lambda item: item[0]))}

logging.info("%d albums identified", len(album_to_assets))
logging.info("Album list: %s", list(album_to_assets.keys()))
if not unattended:
    print("Press Enter to continue, Ctrl+C to abort")
    input()

album_to_id = {}

logging.info("Listing existing albums on immich")

albums = fetchAlbums()
album_to_id = {album['albumName']: album['id'] for album in albums}
logging.info("%d existing albums identified", len(albums))

logging.info("Creating albums if needed")
cpt = 0
for album in album_to_assets:
    if album in album_to_id:
        continue
    album_to_id[album] = createAlbum(album)
    logging.info('Album %s added!', album)
    cpt += 1
logging.info("%d albums created", cpt)

logging.info("Adding assets to albums")
# Note: Immich manages duplicates without problem,
# so each time we can add all assets to same album, no photo will be duplicated
for album, assets in album_to_assets.items():
    id = album_to_id[album]
    addAssetsToAlbum(id, assets)

for asset_id in assets_to_archive:
    archiveAsset(asset_id)

logging.info("Done!")
