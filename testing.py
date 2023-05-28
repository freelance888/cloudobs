# ===== CLIENT ===== #
from server import Minion

streamer = Minion(port=6006)
streamer.run()

# ===== SERVER ===== #
from models import MinionSettings
from server import Skipper
from util.util import WebsocketResponse

skipper = Skipper(port=5010)

# pull sheets
skipper.obs_config.setup("https://docs.google.com/spreadsheets/d/"
                         "10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME/edit#gid=404124283", "Master (копия)")
minion_configs = skipper.obs_config.obs_sheets.pull()
for lang in minion_configs:
    skipper.registry.update_minion(lang, minion_configs[lang])

# infrastructure
skipper.infrastructure.set_ip_langs({"5.161.208.17": "Bel"})
#skipper.infrastructure.spawner.ensure_langs(skipper.registry.list_langs(), wait_for_provision=True)

skipper.registry.minion_configs["Bel"].addr_config.minion_server_addr = "localhost"
ip = skipper.infrastructure.spawner.ip_dict.ip_list()[0][1]
skipper.registry.minion_configs["Bel"].addr_config.obs_host = ip

for lang, server_ip in skipper.infrastructure.spawner.ip_dict.ip_list():
    # if lang not in skipper.minions:
    skipper.minions[lang] = Skipper.Minion(minion_ip="localhost", lang=lang, skipper=skipper,
                                               ws_port=6006)

# apply configs
configs_to_activate = [
    [lang, minion_config]
    for lang, minion_config in skipper.registry.minion_configs.items()
    if not minion_config.active()
]
# collect websocket responses
responses = [
    [lang, skipper.minions[lang].apply_config(minion_config=minion_config)]
    for lang, minion_config in configs_to_activate
    if lang in skipper.minions
]
# wait until websocket callback or timeout
WebsocketResponse.wait_for(responses=[r for _, r in responses])

"""
sudo apt-get update
sudo apt-get install \
    ca-certificates \
    curl \
    gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
sudo install -m 0755 -d /etc/apt/keyrings
 curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
 sudo chmod a+r /etc/apt/keyrings/docker.gpg
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker
sudo systemctl enable docker.service
sudo systemctl enable containerd.service

git clone -b dev https://github.com/ALLATRA-IT/cloudobs.git
cd cloudobs && docker build -t $(basename $(pwd))-dev . --no-cache
docker save $(basename $(pwd))-dev > cloudobs-dev.tar
"""

"""
07.05.2023


import obswebsocket as obsws
import obswebsocket.requests

ip = "5.161.214.30"

obs = obsws.obsws(host=ip, port=4439, timeout=5)
obs.connect()

response = obs.call(obsws.requests.GetCurrentScene())

from obs import OBSController2
obs_controller = OBSController2(obs_host=ip, obs_port=4439)

"""
