## SERVER INITIALIZATION
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
   - `sheet_url` - google sheet url (`server_langs` should not be specified)
   - `worksheet_name` - google sheet worksheet name, (should be specified along
     with `sheet_url`)
   - `force_deploy_minions` - specifies if the server should force redeploy
     minions. Usually it deploys minions only if the server was not woken up
     (if you have not called `POST /wakeup` route yet). So that if you set
     `force_deploy_minions=true` -> the server will redeploy minions.
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /init`
 - Returns current `server_lang` variable of the server (see `GET /info`)
 - Works even if the server was not initialized, in this case the function
   returns data with `host_url` filled.
### `GET /state`
 - Returns current server state
 - Possible values: `["sleeping", "initializing", "not initialized", 
                      "running", "disposing"]`
### `GET /info`
 - Returns current server state.
 - Works even if the server was not initialized, in this case the function
   returns data with `host_url` filled, plus default values are filled for
   all parameters.
 - Has the following format (*default values are specified*):
```
{
    "lang": {
        "server_langs": {
            "host_url": "http://255.255.255.255:6000",
            "websocket_port": 4439,
            "password": "",
            "original_media_url": "",
        },
        "stream_settings": {
            "server": "",
            "key": "",
        },
        "stream_on": {
            "value": False,             # this parameters points if the stream is on
        },
        "media_schedule": {
            #TBA
        },
        "ts_offset": {
            "value": 4000,
        },
        "ts_volume": {
            "value": 0,
        },
        "source_volume": {
            "value": 0,
        },
        "sidechain": {
            "ratio": 32,
            "release_time": 1000,
            "threshold": -30.0,
            "output_gain": -10.0,
        },
        "transition": {
            "transition_name": "Cut",
            "path": "",
            "transition_point": 3600,
        },
        "gdrive_settings": {
            "drive_id": "",
            "media_dir": "",
            "api_key": "",
            "sync_seconds": 0,
            "gdrive_sync_addr": "",
            "objvers": "",
        }
    }
}
```
### `POST /cleanup`
 - Cleans up the server: stop streaming -> reset scenes -> close obs connections
### `DELETE /minions/delete_vms`
 - Deletes all the minion servers. Puts the server into "sleeping" state.
### `POST /sheets/pull`
 - Pulls data from Google Sheets (only available if the server was initialized using
   Google Sheets url) and synchronizes the server according to the sheets dataframe.
### `POST /sheets/push`
 - Pushes data from the server to Google Sheets
## MEDIA TIMING SCHEDULE API
### `POST /media/schedule/setup`
 - Sets up google sheets page info
 - This API should be called before all other `/media/schedule` routes
 - API parameters:
   - `sheet_url` - url of google sheet (required)
   - `sheet_name` - google sheets page name (required)
### `POST /media/schedule/pull`
 - Pulls the schedule from google sheets. Saves it in some buffer, which
   is supposed to be used in `POST /media/schedule`
### `POST /media/schedule`
 - Schedules media play (note that the schedule should be pulled from
   google sheets in advance by using `POST /media/schedule/pull`)
### `GET /media/schedule`
 - Returns current media schedule
 - Has the following format:
```
{
  id_1: {
    "name": "...",
    "timestamp": ...,
    "is_enabled": true/false,
    "is_played": true/false  # this attribute shows if the
                             # video was already played
  },
  id_2: {
    ...
  },
  ...
}
```
### `PUT /media/schedule`
 - Updates media schedule
 - Accepts the following parameters:
   - `id` - schedule id, required (see `GET /media/schedule`)
   - `name` - new video name, optional
   - `timestamp` - new timestamp, optional (format of `hh:mm:ss`)
   - `is_enabled` - enables/disables specified schedule, optional
### `DELETE /media/schedule`
 - Removes current media schedule
## MEDIA PLAY
### `POST /media/play`
 - Plays media (video/audio)
 - Accepts the following parameters:
   - `params` - json dictionary, by-lang parameters, e.g.:
    ```
    {"lang": {"name": "...", "search_by_num": "0/1", "mode": "..."}, ...}
    ```
     - `name` - the name of the video
     - `search_by_num` - points the server needs to search a file by 
       first `n` numbers in name, for example if `name="001_test.mp4"`, 
       the server will search for a file which full name even does not 
       match `001_test.mp4`, but it's name starts with `001`, it may 
       be `001_test2.mp4`, `001.mp4`, etc.
     - `mode` - media play mode. Possible values:
       - `force` - stop any media being played right now, and play 
                   media specified (default value)
       - `check_any` - if any video is being played, skip
       - `check_same` - if the same video is being played, skip, otherwise - play
 - Note: you may set `params` for all languages,
   specifying `__all__` as a lang code, e.g.: `{"__all__": ...}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `DELETE /media/play`
 - Stops playing media
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
## STREAMING
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
## VOLUMES, OFFSETS AND SOURCES
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
### `PUT /source/refresh`
 - Refreshes source for specified languages
 - Accepts the following parameters:
   - `langs` - json list of langs, e.g.:
   ```
   ["eng", "rus"], or ["__all__"] (default)
   ```
 - Note: you may set `langs` for all languages,
   specifying `["__all__"]` as input
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `POST /filters/sidechain`
 - Sets up sidechain
 - Accepts the following parameters:
   - `sidechain_settings` - json dictionary, e.g.:
    ```
    {"lang": {"ratio": ..., "release_time": ..., "threshold": ..., "output_gain": ...}, ...}
    ```
   - Sidechain settings supports the following attributes:
     - `ratio`
     - `release_time`
     - `threshold`
     - `output_gain`
 - Note: you are not required to provide all sidechain params (ratio, release_time, ...).
   If some are not provided, default values will be used.
 - Note: you may specify sidechain settings for all languages,
   passing `__all__` as a lang code, e.g.: `{"__all__": {...}}`
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
## GOOGLE DRIVE API
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
## VMIX PLAYERS API
### `GET /vmix/players`
 - Returns posted vmix players (ip addresses which are allowed to play video)
 - Returns a list of dicts with the format `{"ip": "...", "label": "...",
   "active": true/false}`, e.g.:
```
[
   {"ip": "1.2.3.4", "label": "Локация 1", "active": true},
   {"ip": "1.2.3.5", "label": "Локация 2", "active": false},
   {"ip": "1.2.3.6", "label": "Локация 3", "active": false}
]
```
 - Note that if active ip is set to `"*"` this route will return all ip addresses
   allowed to play video
### `POST /vmix/players`
 - Posts vmix players ip addresses
 - Accepts the following parameters:
   - `ip_list` - list of dicts with the format `{"ip": "...", "label": "..."}`,
     e.g.:
```
[
   {"ip": "1.2.3.4", "label": "Локация 1"},
   {"ip": "1.2.3.5", "label": "Локация 2"},
   {"ip": "1.2.3.6", "label": "Локация 3"}
]
```
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
### `GET /vmix/players/active`
 - Returns current active vmix player (if not specified yet -> `"*"` is returned).
 - E.g.: `1.2.3.4`, or `*`
### `POST /vmix/players/active`
 - Sets active vmix player
 - Accepts the following parameters:
   - `ip` - ip address
 - if `ip` is not in the list posted using `POST /vmix/players` - exception is
   raised
 - `ip` also can be set to `*` -> this allows all ip addresses to play video
 - Returns `("Ok", 200)` on success, otherwise `("error details", 500)`
