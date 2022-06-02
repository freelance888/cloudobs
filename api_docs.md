### `POST /init`
 - Initializes the server
 - Accepts the following parameters:
   - `server_langs` -
    json, dict of 
     ```
     "lang": {
        "host_url": "localhost",  # url of an instance_service
        "websocket_port": 1234,   # obs websocket port
        "password": "qwerty123",  # obs websocket password (not working yet)
        "original_media_url": "srt://localhost"  # rtmp/srt source url
     }     
     ```
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /init`
 - Returns current `server_lang` variable of the server (see `POST /init`)
### `POST /cleanup`
 - Cleans up the server: stop streaming -> reset scenes -> close obs connections
### `POST /media/schedule`
 - Schedules media play
 - Accepts the following parameters:
   - `schedule` - json list, e.g.:
   ```
   [..., [name, timestamp], ...]
   ```
 - `timestamp` - floating-point number, in seconds
 - name media-file name, used `search_by_num=1` (see `POST /media/play`)
### `POST /media/play`
 - Plays media (video/audio)
 - Accepts the following parameters:
   - `params` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": {"name": "...", "search_by_num": "0/1"}, ...}
    ```
   where `name` is the name of the video, `search_by_num` - 
   points the server needs to search a file by first `n` numbers in name,
   for example if `name="001_test.mp4"`, the server will search for a file
   which full name even does not match `001_test.mp4`, but it's name starts with
   `001`, it may be `001_test2.mp4`, `001.mp4`, etc.
 - Note: you may set `params` for all languages,
   specifying `__all__` as a lang code, e.g.: `{"__all__": ...}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /stream/settings`
 - Sets streaming destination settings
 - Accepts the following parameters:
   - `stream_settings` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": {"server": "rtmp://...", "key": "..."}, ...}
    ```
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /stream/start`
 - Starts streaming
 - Accepts the following parameters:
   - `langs` - json list of langs, e.g.:
   ```
   ["eng", "rus"], or ["__all__"] (default)
   ```
 - Note: you may set `langs` for all languages,
   specifying `["__all__"]` as input
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /stream/stop`
 - Stops streaming
 - Accepts the following parameters:
   - `langs` - json list of langs, e.g.:
   ```
   ["eng", "rus"], or ["__all__"] (default)
   ```
 - Note: you may set `langs` for all languages,
   specifying `["__all__"]` as input
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /ts/offset`
 - Sets teamspeak sound offset (in milliseconds)
 - Accepts the following parameters:
   - `offset_settings` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": offset, ...}
    ```
 - Note: you may specify one offset for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": offset}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /ts/offset`
 - Returns current teamspeak sound offset (in milliseconds)
 - Has the following structure:
   ```
   {"lang": offset, ...}
   ```
 - Returns ("data", 200)
### `POST /ts/volume`
 - Sets teamspeak sound volume (in decibels)
 - Accepts the following parameters:
   - `volume_settings` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": volume, ...}
    ```
 - Note: you may specify one volume for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": volume}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /ts/volume`
 - Returns current teamspeak volume (in decibels)
 - Has the following structure:
   ```
   {"lang": volume, ...}
   ```
 - Returns ("data", 200)
### `POST /source/volume`
 - Sets original source sound volume (in decibels)
 - Accepts the following parameters:
   - `volume_settings` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": volume, ...}
    ```
 - Note: you may specify one volume for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": volume}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /source/volume`
 - Returns current original source volume (in decibels)
 - Has the following structure:
   ```
   {"lang": volume, ...}
   ```
 - Returns ("data", 200)
### `POST /filters/sidechain`
 - Sets up sidechain
 - Accepts the following parameters:
   - `sidechain_settings` - json dictionary, e.g.:
    ```
    {"lang": {"ratio": ..., "release_time": ..., "threshold": ...}, ...}
    ```
 - Note: you are not required to provide all sidechain params (ratio, release_time, ...).
   If some are not provided, default values will be used.
 - Note: you may specify sidechain settings for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": {...}}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /transition`
 - Sets up transition
 - Accepts the following parameters:
   - `transition_settings` - json dictionary, e.g.:
    ```
    {"lang": {"transition_name": ..., "transition_point": ..., path": ...}, ...}
    ```
 - The following transition settings are supported:
   ```
   transition_name      - supported values are ("Cut", "Stinger"); required
   transition_point     - transition point in ms; required for "Stinger",
                          optional for "Cut" (used as delay for "Cut")
   path                 - the name of media file to use as transition 
                          (e.g. "stinger_1.mp4"); required for "Stinger"
   ```
   If some are not provided, default values will be used.
 - Note: you may specify transition settings for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": ...}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /gdrive/sync`
 - Initializes google drive files downloading
 - Accepts the following parameters:
   - `gdrive_settings` - json dictionary, e.g.:
    ```
    {
        "lang": {
            "drive_id": "...",         # google drive folder id
            "media_dir": "...",        # local media directory to save media (default /home/stream/content)
            "api_key": "...",          # google api key
            "sync_seconds": "...",     # sync files every ... seconds
            "gdrive_sync_addr": "..."  # gdrive_sync service address (default http://localhost:7000)
        }
    }
    ```
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /gdrive/files`
 - Returns information about google drive files
 - Accepts the following parameters:
   - `return_details` - `"1/0"` (default `0`), points if needed to return 
     detailed info for all languages. Values:
     - `0` - returns 
       ```
       {"__all__": [... [filename, true/false - loaded/not loaded], ...]}
       ```
     - `1` - returns
       ```
       {"lang": [... [filename, true/false - at least loaded on one server (or not)], ...]}
       ```