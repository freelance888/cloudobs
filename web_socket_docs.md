# Common
 - Every command should contain at least `command`
 - You cannot query change events, only subscribe to those changes

# Change Events

###  - `on_registry_change`
 - **Description:** Triggers on every registry change.
 - **Returns:**
```json
{
  "registry": models.Registry().dict()
}
```
------------------------------------------------------------------------------

# Commands
## Skipper commands

###  - `pull config`
 - **Description:** Pulls configuration from google sheets.
 - **Parameters:**
   - `sheet_url` - url of google sheets.
   - `sheet_name` - google sheet name.
   - `langs` - specifies which langs should be pulled from google sheet.
   - `ip_langs` - will set up infrastructure. This parameter's is used
                    for **manual server deployment**.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - if `sheet_url` or `sheet_name` are not specified (both) skipper
     will use cached ones from last time (it is preferred to specify
     them only once).
   - If `langs` is specified - the server will take into account only those langs
     which are specified. Note that if minions were deployed, and `langs`
     specifies list of langs which is less than the one in sheets
     (or just one lang), the server will not delete minions outside this list.
   - If `ip_langs` is not specified - the server will deploy minions itself
     using the configuration specified in google sheets.
   - If `ip_langs` is specified - after initialization -> skipper locks
     infrastructure.
 - **Command example (json):**
```json
{
  "command": "pull config",
  "details": {
    "sheet_url": "url",
    "sheet_name": "name",
    "langs": ["lang1", "lang2", ...],
    "ip_langs": {... "ip": "lang", ...}
  }
}
```
------------------------------------------------------------------------------
###  - `dispose`
 - **Description:** Deletes minions.
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "dispose"
}
```
------------------------------------------------------------------------------
###  - `get info`
 - **Description:** Returns current server registry.
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": {
    "registry": See models.Registry().dict()
  }
}
```
 - **Notes:**
   - registry - is the object which contains all the information
     you need. It contains minions configurations, cached google sheets,
     vmix players, infrastructure lock, timing and server state.
     See models.Registry
 - **Command example (json):**
```json
{
  "command": "get info"
}
```
------------------------------------------------------------------------------
###  - `set stream settings`
 - **Description:** Sets stream settings.
 - **Parameters:**
   - `server` - target server address.
   - `key` - target stream key.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "set stream settings",
  "details": {"server": "...", "key": "..."},
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `set teamspeak offset`
 - **Description:** Sets teamspeak offset.
 - **Parameters:**
   - `value` - target value in milliseconds.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "set teamspeak offset",
  "details": {"value": numeric_value},
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `set teamspeak volume`
 - **Description:** Sets teamspeak volume.
 - **Parameters:**
   - `value` - target value in decibels.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "set teamspeak volume",
  "details": {"value": numeric_value},
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `set source volume`
 - **Description:** Sets source volume.
 - **Parameters:**
   - `value` - target value in decibels.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "set source volume",
  "details": {"value": numeric_value},
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `set sidechain settings`
 - **Description:** Sets sidechain settings.
 - **Parameters:**
   - `ratio` - target ratio.
   - `release_time` - target release time.
   - `threshold` - target threshold.
   - `output_gain` - target output gain.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - You may specify only those parameters you want to change (not all).
 - **Command example (json):**
```json
{
  "command": "set sidechain settings",
  "details": {
    "ratio": ..., "release_time": ..., "threshold": ..., "output_gain": ...
  },
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `set transition settings`
 - **Description:** Sets transition settings.
 - **Parameters:**
   - `transition_point` - target transition point in milliseconds.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "set transition settings",
  "details": {
    "transition_point": numeric_value
  },
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `infrastructure lock`
 - **Description:** Locks infrastructure change. When you lock
   the infrastructure, no minions can be spawned (or replaced).
   That means, if you made an error in google sheets accidentally,
   the server won't change the minions infrastructure.
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "infrastructure lock"
}
```
------------------------------------------------------------------------------
###  - `infrastructure unlock`
 - **Description:** Unlocks the infrastructure (see 'lock infrastructure' for
   more details).
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "infrastructure unlock"
}
```
------------------------------------------------------------------------------
###  - `vmix players add`
 - **Description:** Adds a new vmix player to the registry.
 - **Parameters:**
   - `ip` - vmix player's ip address.
   - `name` - vmix player's friendly name.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "vmix players add"
  "details": {
    "ip": "ip address",
    "name": "... Moscow ..."
  }
}
```
------------------------------------------------------------------------------
###  - `vmix players remove`
 - **Description:** Removes vmix player from the registry.
 - **Parameters:**
   - `ip` - vmix player's ip address.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - You cannot delete vmix player with ip `*`
 - **Command example (json):**
```json
{
  "command": "vmix players remove"
  "details": {
    "ip": "ip address"
  }
}
```
------------------------------------------------------------------------------
###  - `vmix players set active`
 - **Description:** Sets active vmix player.
 - **Parameters:**
   - `ip` - vmix player's ip address.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - You may specify `*` as an ip address - that will allow all ip
     addresses. By default - active ip is `*`
 - **Command example (json):**
```json
{
  "command": "vmix players set active"
  "details": {
    "ip": "ip address"
  }
}
```
------------------------------------------------------------------------------
###  - `start streaming`
 - **Description:** Starts the streaming.
 - **Parameters:**
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "start streaming",
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `stop streaming`
 - **Description:** Stops the streaming.
 - **Parameters:**
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "stop streaming",
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `pull timing`
 - **Description:** Pulls configuration from google sheets for the timing.
 - **Parameters:**
   - `sheet_url` - url of google sheets.
   - `sheet_name` - google sheet name.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
    - if `sheet_url` or `sheet_name` are not specified (both) skipper
     will use cached ones from last time (it is preferred to specify them only once).
 - **Command example (json):**
```json
{
  "command": "pull timing",
  "details": {
    "sheet_url": "url",
    "sheet_name": "name"
  }
}
```
------------------------------------------------------------------------------
###  - `run timing`
 - **Description:** Runs the timing pulled.
 - **Parameters:**
   - `countdown` - countdown to run the timing in the following format: `%H:%M:%S`.
   - `daytime` - runs the timing in the specified daytime.
     Daytime should be in the following format: `%H:%M:%S`.
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - If both `countdown` and `daytime` are specified - `countdown` only used.
   - If `daytime` (time) is less than actual time on the server,
     for example, `daytime` is `04:12:01` and the time on the server is
     `2023.01.10 20:03:10` -> the timing will run next day on time
     `2023.01.11 04:12:01`.
   - If not parameters are specified - the timing runs instantly.
 - **Command example (json):**
```json
{
  "command": "run timing",
  "details": {
    "daytime": "20:00:00"
  }
}
```
------------------------------------------------------------------------------
###  - `stop timing`
 - **Description:** Stops (resets) the timing.
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - Also stops the video playing.
 - **Command example (json):**
```json
{
  "command": "stop timing"
}
```
------------------------------------------------------------------------------
###  - `remove timing`
 - **Description:** Stops the timing and removes it.
 - **Parameters:**
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
   - Timing also stops the video playing.
 - **Command example (json):**
```json
{
  "command": "remove timing"
}
```
------------------------------------------------------------------------------
###  - `play media`
 - **Description:** Plays the media.
 - **Parameters:**
   - `name` - name of the media.
   - `search_by_num` - points if to search the video by its leading numbers.
     Available values: `true/false`
   - `mode` - media run mode. Available modes:
     - `force` - stops any playing media and plays the specified one (default).
     - `check_any` - plays specified media only if no media is being played.
     - `check_same` - plays specified media only if it is not being played.
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "play media",
  "details": {
    "name": "01_video_rus.mp4",
    "search_by_num": true,
    "mode": "check_same"
  },
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `stop media`
 - **Description:** Stops the playing media.
 - **Parameters:**
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "stop media",
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `refresh source`
 - **Description:** Refreshes original media source.
 - **Parameters:**
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": null
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "refresh source",
  "lang": "Rus"
}
```
------------------------------------------------------------------------------
###  - `list gdrive files`
 - **Description:** Lists google drive files.
 - **Parameters:**
   - `lang` - use this parameter to specify a language. By default,
     all languages are affected (optional parameter).
 - **Returns:**
```json
{
  "result": true/false,
  "details": "... message ...",
  "serializable_object": {
    "lang": {
      "result": true/false,
      "details": "... message ...",
      "serializable_object": {
        "01_video_rus.mp4": true,  # true - downloaded
        "02_audio_eng.mp3": false,  # false - not downloaded yet
        ...
      }
    }
  }
}
```
 - **Notes:**
 - **Command example (json):**
```json
{
  "command": "list gdrive files",
  "lang": "Rus"
}
```
------------------------------------------------------------------------------