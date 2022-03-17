import time
import json
import os
import obswebsocket as obsws
import obswebsocket.requests
import server
from flask import Flask
from flask import request
from dotenv import load_dotenv

load_dotenv()
MEDIA_DIR = os.getenv('MEDIA_DIR')

app = Flask(__name__)
obs_server: server.Server = None

@app.route('/init', methods=['POST'])
def init():
    """
    Query parameters:
    server_langs: json, dict of "lang": {"obs_host": "localhost", "websocket_port": 1234, "password": "qwerty123", "original_media_url": "srt://localhost"}}
    e.g.: {"rus": {"obs_host": "localhost", "websocket_port": 1234, "password": "qwerty123", "original_media_url": "srt://localhost"}, "eng": ...}
    :return:
    """
    server_langs = request.args.get('server_langs', '')
    server_langs = json.loads(server_langs)

    # validate `server_langs`
    for lang, lang_info in server_langs.items():
        for attr in ['obs_host', 'websocket_port', 'password', 'original_media_url']:
            if attr not in lang_info:
                return 500, f"Please specify `{attr}` for lang '{lang}'"

        websockets_port = lang_info['websockets_port']

        # TODO: validate `obs_host`
        if not str(websockets_port).isdigit():
            return 500, "`websocket_port` must be a number"
        # TODO: validate original_media_url

    global obs_server
    obs_server = server.Server(server_langs=server_langs, base_media_path=MEDIA_DIR)
    status, err_msg = obs_server.initialize()

    return 200 if status else 500, err_msg

@app.route('/media/play', methods=['POST'])
def media_play():
    """
    Query parameters:
    name: name of the video
    e.g.: 001_video_desc.mp4
    :return:
    """
    name = request.args.get('name', None)
    if not name:
        return 500, "`name` must not be empty"

    result = obs_server.run_media(name)

    return 200 if result else 500, ''



# ==================================== FOR TESTING
client = obswebsocket.obsws("localhost", 4441)
# client.register(on_event)
client.connect()

# client.call(obswebsocket.requests.GetVersion()).getObsWebsocketVersion()
client.call(obswebsocket.requests.GetSourcesList()).getSources()
client.call(obswebsocket.requests.GetSourceSettings('original_media')).getSourceSettings()
obswebsocket.requests.CreateSource(sourceName='name', sourceKind='', sceneName='', sourceSettings='')
client.call(obswebsocket.requests.GetSceneList()).getScenes()

client.call(obswebsocket.requests.GetMute('original_stream')).getMuted()
client.call(obswebsocket.requests.GetAudioMonitorType('original_stream')).getMonitorType()  # none, monitorOnly, monitorAndOutput
client.call(obswebsocket.requests.SetAudioMonitorType(sourceName='original_stream', monitorType='none'))

'''
{'input': 'rtmp://nsk-2.facecast.io/re/861424dbf89b93e52333',
 'is_local_file': False}
 '''
items = client.call(obswebsocket.requests.GetSceneItemList(sceneName='main_1')).getSceneItems()
for item in items:
    print(item)
    client.call(obswebsocket.requests.DeleteSceneItem(item=item))


try:
    time.sleep(100)

except KeyboardInterrupt:
    pass

client.disconnect()
obswebsocket.requests.GetSourceSettings