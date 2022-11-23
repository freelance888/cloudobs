import json
import logging
import os
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
SA_DEPLOY_IP = os.getenv("SA_DEPLOY_IP", None)
SA_DEPLOY_PORT = os.getenv("SA_DEPLOY_PORT", None)


class IPDict:
    def __init__(self):
        self._ip_list = {}  # ip: lang

    def add_ip_if_not_exists(self, ip):
        if ip not in self._ip_list:
            self._ip_list[ip] = ""

    def set_ip_lang(self, ip, lang):
        if not lang:
            self.reset_ip_lang(ip)
            return
        for _ip, _lang in self._ip_list.items():
            if _ip != ip and _lang == lang:
                raise ValueError("Lang is already bound for another ip")
        self.add_ip_if_not_exists(ip)
        self._ip_list[ip] = lang

    def reset_ip_lang(self, ip):
        self.add_ip_if_not_exists(ip)
        self._ip_list[ip] = ""

    def remove_lang_if_exists(self, lang):
        for ip in self._ip_list.keys():
            if self._ip_list[ip] == lang:
                self.reset_ip_lang(ip)

    def remove_ip(self, ip):
        if ip in self._ip_list:
            self._ip_list.pop(ip)

    def ip_has_lang(self, ip):
        return ip in self._ip_list

    def get_ip_lang(self, ip):
        if ip not in self._ip_list:
            raise KeyError(f"IP: {ip}")
        return self._ip_list[ip]

    def get_lang_ip(self, lang):
        for ip, _lang in self._ip_list.items():
            if _lang == lang:
                return ip
        raise KeyError(f"Lang: {lang}")

    def list_ips(self):
        return list(self._ip_list.keys())

    def list_free_ips(self):
        return [ip for ip, lang in self._ip_list.items() if not lang]

    def list_langs(self):
        """
        :return: List
        """
        return [lang for ip, lang in self._ip_list.items() if lang]

    def ip_list(self):
        """
        :return: list of [... [lang, ip], ...]
        """
        return [[lang, ip] for ip, lang in self._ip_list.items()]

    def cleanup(self):
        for ip in list(self._ip_list.keys()):
            self.remove_ip(ip)


class SSHContext:
    def __init__(self, ip):
        self.ip = ip

    def request(self, api, method, params=None):
        """
        :param api: function name, e.g.: "hcloud_context_list"
        :param method: http method, available values: ["POST", "GET"]
        :param params: dict
        :return:
        """
        if params:
            params = urlencode(params)
            request = f"http://{SA_DEPLOY_IP}:{SA_DEPLOY_PORT}/minions/{api}?{params}"
        else:
            request = f"http://{SA_DEPLOY_IP}:{SA_DEPLOY_PORT}/minions/{api}"

        if method == "POST":
            response = requests.post(request)
        elif method == "GET":
            response = requests.get(request)
        else:
            raise ValueError(f"Method: {method}")

        if response.status_code != 200:
            raise RuntimeError(f"E PYSERVER::SSHContext::request: {response.text}")
        return json.loads(response.text)

    def hcloud_context_list(self):
        return self.request("hcloud_context_list", "GET")

    def hcloud_context_use(self, name):
        return self.request("hcloud_context_use", "POST", {"name": name})

    def ensure_vms(self, total_num_vms):
        return self.request("create_vm", "POST", {"count": json.dumps(total_num_vms)})

    def get_ip(self):
        return self.request("get_ip", "GET")

    def delete_vms(self):
        return self.request("delete_vms", "POST")

    def provision(self, ip_list):
        return self.request("provision", "POST", {"ip_list": json.dumps(ip_list)})

    def check_provision(self, ip):
        return self.request("check_provision", "GET", {"ip": ip})


class Minions:
    def __init__(self):
        self.ssh_context = SSHContext(ip=SA_DEPLOY_IP)
        self.ip_dict = IPDict()

    def _ensure_minions(self, n_minions):
        assert n_minions > 0, "`n_minions` cannot be less than 1"

        ips = self.ssh_context.get_ip()  # get current ip list state
        if len(ips) < n_minions:  # ip ips count is less then `n_minions` create ones
            ips = self.ssh_context.ensure_vms(n_minions)
        if len(ips) < n_minions:  # if for some reason couldn't create those minions, raise exception
            raise RuntimeError("Couldn't deploy enough minions")

        for ip in ips:
            self.ip_dict.add_ip_if_not_exists(ip)

    def ensure_langs(self, langs, wait_for_provision=False, provision_timeout=300):
        """
        :param langs: list of langs
        :return: list of [..., [lang, ip], ...] which are about being provisioned
        """
        # make sure we have enough minions deployed
        self._ensure_minions(len(langs))
        # remove old languages which are not used anymore
        for lang in self.ip_dict.list_langs():
            if lang not in langs:  # if already bound (before) lang is not listed in new `langs` param
                # TODO: cleanup a minion
                self.ip_dict.remove_lang_if_exists(lang)  # remove lang
        # add new languages
        # first find free ips addresses
        free_ips = self.ip_dict.list_free_ips()
        bound_langs = self.ip_dict.list_langs()

        ip_list_for_provision = []  # [..., [lang, ip], ...]
        for lang in langs:
            if lang not in bound_langs:  # if the language has not been bound to an IP yet
                if len(free_ips) == 0:
                    raise RuntimeError(f"No minions available for new langs. Something went wrong. Lang: {lang}")
                ip = free_ips[0]
                free_ips.remove(ip)
                self.ip_dict.set_ip_lang(ip, lang)  # bind lang to an IP
                ip_list_for_provision.append([lang, ip])

        if len(ip_list_for_provision) > 0:
            time.sleep(10)
            self.ssh_context.provision(ip_list_for_provision)  # provision for those servers
            # if needed to wait until provision
            if wait_for_provision:
                try:
                    self.wait_until_provision(timeout=provision_timeout)
                except TimeoutError as ex:
                    # remove langs (revert changes) if the function threw provision timeout error
                    for lang, ip in ip_list_for_provision:
                        self.ip_dict.remove_lang_if_exists(lang)
                    raise ex
        return self.ip_dict.ip_list()

    def cleanup(self):
        self.ip_dict.cleanup()
        try:
            self.ssh_context.delete_vms()
        except Exception as ex:
            logger.warning(f"Failed to cleanup: {ex}")
            return False
        return True

    def wait_until_provision(self, timeout=300):
        time_start = time.time()
        provision_status = self.check_provision()
        while not all([status for lang, status in provision_status.items()]):
            time.sleep(10)
            if (time.time() - time_start) > timeout:
                raise TimeoutError("Provision timeout")
            provision_status = self.check_provision()

    def check_provision(self):
        """
        Checks all the minions if the provision is finished. Returns a dictionary of (lang, True/False)
        :return:
        """
        # list all langs which is used for restream
        langs = self.ip_dict.list_langs()
        # convert a list of langs into a dictionary of (lang: ip)
        ips = {lang: self.ip_dict.get_lang_ip(lang) for lang in langs}
        # check provision status. it only checks for `cat ~/PROVISION_STATUS` == "DONE" on every minion
        return {lang: self.ssh_context.check_provision(ip) for lang, ip in ips.items()}
