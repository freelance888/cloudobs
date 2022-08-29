import os, sys, time

SA_DEPLOY_IP = "65.109.3.38"
CMD_HCLOUD_LIST = "ssh -o StrictHostKeyChecking=no user@{ip} " \
                  "\"hcloud context list\""
CMD_HCLOUD_USE = "ssh -o StrictHostKeyChecking=no user@{ip} " \
                 "\"hcloud context use {name}\""
CMD_CREATE_VM = "ssh -o StrictHostKeyChecking=no user@{ip} " \
                "\"cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
                "./init.sh --create-vm {num_vms}\""
CMD_GET_IP = "ssh -o StrictHostKeyChecking=no user@{ip} " \
             "\"cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
             "./init.sh --getip\""
CMD_DELETE_VMS = "ssh -o StrictHostKeyChecking=no user@{ip} " \
                 "\"cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
                 "./init.sh -d\""
CMD_UPLOAD_FILES = "ssh -o StrictHostKeyChecking=no user@{ip} -t 'bash -ic " \
                   "\"cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
                   "./init.sh --upload-files\"'"
CMD_PROVISION = "ssh -o StrictHostKeyChecking=no user@{ip} -t 'bash -ic " \
                "\"cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
                "./init.sh --provision\"'"
CMD_UPLOAD_IP_LIST = "scp -o StrictHostKeyChecking=no " \
                     "{ip_list} user@{ip}:/home/user/cloudobs-infrastructure-main/shared/scripts/ip.list"
CMD_CHECK_PROVISION = "ssh -o StrictHostKeyChecking=no stream@{ip} cat /home/stream/PROVISION_STATUS"
IP_LIST_EXAMPLE_PATH = "./ip.list.example"


class IPDict:
    def __init__(self):
        self._ip_list = {}  # ip: lang

    def add_ip(self, ip):
        if ip not in self._ip_list:
            self._ip_list[ip] = ""

    def set_ip_lang(self, ip, lang):
        if not lang:
            self.reset_ip_lang(ip)
            return
        for _ip, _lang in self._ip_list.items():
            if _ip != ip and _lang == lang:
                raise ValueError("Lang is already bound for another ip")
        self.add_ip(ip)
        self._ip_list[ip] = lang

    def reset_ip_lang(self, ip):
        self.add_ip(ip)
        self._ip_list[ip] = ""

    def remove_lang(self, lang):
        for ip in self._ip_list.keys():
            if self._ip_list[ip] == lang:
                self.reset_ip_lang(ip)

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


class SSHContext:
    def __init__(self, ip):
        self.ip = ip

    def hcloud_context_list(self):
        # form a shell command
        cmd = CMD_HCLOUD_LIST.format(ip=self.ip)
        with os.popen(cmd) as fp:
            # read result
            result = fp.read()
            # if none returned, mostly that means we couldn't establish connection
            if not result:
                raise RuntimeError(f"E PYSERVER::SSHContext::hcloud_list(): "
                                   f"Couldn't execute `hcloud context list` command. \nIP: {self.ip}")
            #
            lines = result.split('\n')
            if len(lines) <= 1:
                raise RuntimeError(f"E PYSERVER::SSHContext::hcloud_list(): "
                                   f"`hcloud context list` returned no context. Please check if "
                                   f"you have configured tokens. \nIP: {self.ip}, \nOutput: {result}")
            # `hcloud context list` prints out two columns of data (ACTIVE MAME)
            # skip first entry since this is a header
            lines = [line.split() for line in lines][1:]
            lines = [[len(line) == 2, line[-1]] for line in lines if
                     line]  # len(line) == 2 => that means ACTIVE context
        return lines

    def hcloud_context_use(self, name):
        # form a shell command
        cmd = CMD_HCLOUD_USE.format(ip=self.ip, name=name)
        with os.popen(cmd) as fp:
            pass

    def create_vm(self, total_num_vms):
        # form a shell command
        cmd = CMD_CREATE_VM.format(ip=self.ip, num_vms=total_num_vms)
        with os.popen(cmd) as fp:
            create_vm_result = fp.read()
        return self.get_ip()

    def get_ip(self):
        # form a shell command
        cmd = CMD_GET_IP.format(ip=self.ip)
        with os.popen(cmd) as fp:
            result = fp.read().split('\n')
        return [x for x in result if x]

    def delete_vms(self):
        # form a shell command
        cmd = CMD_DELETE_VMS.format(ip=self.ip)
        with os.popen(cmd) as fp:
            result = fp.read()

    def provision(self, ip_list):
        """
        :param ip_list: list of [..., [lang, ip], ...]
        :return:
        """
        # TODO: log function
        print(f"PYSERVER::Minions::provision(): Provisioning the following langs: {[lang for lang, _ in ip_list]}")
        sys.stdout.flush()

        self.upload_ip_list(ip_list)
        # ./init.sh --upload-files
        cmd = CMD_UPLOAD_FILES.format(ip=self.ip)
        with os.popen(cmd, "r") as fp:
            _ = fp.read()  # wait until it ends
        # ./init.sh --provision
        cmd = CMD_PROVISION.format(ip=self.ip)
        with os.popen(cmd, "r") as fp:
            _ = fp.read()  # wait until it ends

    def check_provision(self, ip):
        """
        Checks provision status for given ip address (minion ip address).
        What the function does is it only checks the local `~/PROVISION_STATUS` file on a minion
        with an `ip` specified.
        :return: True/False
        """
        cmd = CMD_CHECK_PROVISION.format(ip=ip)
        with os.popen(cmd, "r") as fp:
            status = fp.read()  # wait until it is finished
        return "DONE" in status

    def upload_ip_list(self, ip_list):
        """
        :param ip_list: list of [..., [lang, ip], ...]
        :return:
        """
        # form a placeholder in the followind format:
        #   [lang]=ip
        #   [Eng]=1.2.3.4
        #   ...
        placeholder = "\n".join([f"  [{lang}]={ip}" for lang, ip in ip_list])
        # read the template to form ip.list file
        with open(IP_LIST_EXAMPLE_PATH, "rt") as fp:
            template = fp.read()
        ip_list = template.format(placeholder=placeholder)
        with open("./ip.list", "wt") as fp:
            fp.write(ip_list)
        # upload ip.list file onto the server
        cmd = CMD_UPLOAD_IP_LIST.format(ip_list="./ip.list", ip=self.ip)
        with os.popen(cmd, "r") as fp:
            _ = fp.read()  # wait until it ends


class Minions:
    def __init__(self):
        self.ssh_context = SSHContext(ip=SA_DEPLOY_IP)
        self.ip_dict = IPDict()

    def _ensure_minions(self, n_minions):
        assert n_minions > 0, "`n_minions` cannot be less than 1"

        ips = self.ssh_context.get_ip()  # get current ip list state
        if len(ips) < n_minions:  # ip ips count is less then `n_minions` create ones
            ips = self.ssh_context.create_vm(n_minions)
        if len(ips) < n_minions:  # if for some reason couldn't create those minions, raise exception
            raise RuntimeError("Couldn't deploy enough minions")

        for ip in ips:
            self.ip_dict.add_ip(ip)

    def ensure_langs(self, langs, wait_for_provision=False, provision_timeout=300):
        """
        :param langs: list of langs
        :return: list of [..., [lang, ip], ...] which are about being provisioned
        """
        # make sure we have enough minions deployed
        self._ensure_minions(len(langs))
        # remove old languaged which are not used anymore
        for lang in self.ip_dict.list_langs():
            if lang not in langs:  # if already bound (before) lang is not listed in new `langs` param
                # TODO: cleanup a minion
                self.ip_dict.remove_lang(lang)  # remove lang
        # add new languages
        # first find free ips addresses
        free_ips = self.ip_dict.list_free_ips()
        bound_langs = self.ip_dict.list_langs()
        # TODO: ip_list_for_provision as a dict, move cleanup mechanism into provision
        # how?
        # try to pass empty langs in provision maybe?
        # I think it even doesn't matter, what language is passed, just REprovision would be enough
        ip_list_for_provision = []  # [..., [lang, ip], ...]
        for lang in langs:
            if lang not in bound_langs:  # if the language has not been bound yet
                if len(free_ips) == 0:
                    raise RuntimeError(f"No minions available for new langs. Something went wrong. Lang: {lang}")
                ip = free_ips[0]
                free_ips.remove(ip)
                self.ip_dict.set_ip_lang(ip, lang)  # bind a lang to an ip
                ip_list_for_provision.append([lang, ip])

        self.ssh_context.provision(ip_list_for_provision)  # do provision for those servers
        # if needed to wait until provision
        if wait_for_provision:
            try:
                self.wait_until_provision(timeout=provision_timeout)
            except TimeoutError as ex:
                # remove langs (revert changes) if the function threw provision timeout error
                for lang, ip in ip_list_for_provision:
                    self.ip_dict.remove_lang(lang)
                raise ex
        return ip_list_for_provision

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
