# ===== CLIENT ===== #
from server import Minion

streamer = Minion(port=6006)
streamer.run()

# ===== SERVER ===== #
from models import MinionSettings
from server import Skipper
from util.util import WebsocketResponse

skipper = Skipper()

# pull sheets
skipper.obs_config.setup("https://docs.google.com/spreadsheets/d/"
                         "10J2FG-6nKodpXcTVPmNwKGOwGXSxPUWf1MppT7yUgME/edit#gid=404124283", "table_4")
minion_configs = skipper.obs_config.obs_sheets.pull()
for lang in minion_configs:
    skipper.registry.update_minion(lang, minion_configs[lang])

# infrastructure
skipper.infrastructure.spawner.ensure_langs(skipper.registry.list_langs(), wait_for_provision=True)

skipper.registry.minion_configs["Bel"].addr_config.minion_server_addr = "localhost"
ip = skipper.infrastructure.spawner.ip_dict.ip_list()[0][1]
skipper.registry.minion_configs["Bel"].addr_config.obs_host = ip

for lang, server_ip in skipper.infrastructure.spawner.ip_dict.ip_list():
    if lang not in skipper.minions:
        skipper.minions[lang] = Skipper.Minion(minion_ip="localhost", lang=lang)

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

"""