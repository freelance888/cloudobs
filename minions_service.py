import json
import logging
import os
import sys
import time

from flask import Flask, request

from util.util import ExecutionStatus

app = Flask(__name__)
logger = logging.getLogger(__name__)

CMD_HCLOUD_LIST = "hcloud context list"
CMD_HCLOUD_USE = "hcloud context use {name}"
CMD_CREATE_VM = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
                "./init.sh --cloud hetzner --create-vm {num_vms}"
CMD_GET_IP = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud hetzner --getip"
CMD_DELETE_VMS = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud hetzner -d"
CMD_UPLOAD_FILES = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --upload-files"
CMD_PROVISION = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --provision"
CMD_UPLOAD_IP_LIST = "cp {ip_list} /home/user/cloudobs-infrastructure-main/shared/scripts/ip.list"
CMD_CHECK_PROVISION = "ssh -o StrictHostKeyChecking=no stream@{ip} cat /home/stream/PROVISION_STATUS"
IP_LIST_EXAMPLE_PATH = "./ip.list.example"


class CMDContext:
    def __init__(self):
        pass

    def popen(self, cmd, num_retries=3):
        count_attempts = 0

        while count_attempts <= num_retries:
            with os.popen(cmd) as fp:
                result = fp.read()
                return_code = fp.close()
                if return_code is None:
                    return_code = 0
                count_attempts += 1

                if return_code == 0:
                    return result

                time.sleep(5)
        raise RuntimeError(f'Cannot execute command: "{cmd}"')

    def hcloud_context_list(self):
        # form a shell command
        cmd = CMD_HCLOUD_LIST
        result = self.popen(cmd)
        # if none returned, mostly that means we couldn't establish connection
        if not result:
            raise RuntimeError("E PYSERVER::CMDContext::hcloud_list(): Couldn't execute `hcloud context list` command.")
        #
        lines = result.split("\n")
        if len(lines) <= 1:
            raise RuntimeError(
                "E PYSERVER::CMDContext::hcloud_list(): "
                "`hcloud context list` returned no context. Please check if "
                f"you have configured tokens, \nOutput: {result}"
            )
        # `hcloud context list` prints out two columns of data (ACTIVE MAME)
        # skip first entry since this is a header
        lines = [line.split() for line in lines][1:]
        lines = [[len(line) == 2, line[-1]] for line in lines if line]  # len(line) == 2 => that means ACTIVE context
        return lines  # returns a list of [... [[active], ip], ...] -- `active` variable is only filled in active ip

    def hcloud_context_use(self, name):
        # form a shell command
        cmd = CMD_HCLOUD_USE.format(name=name)
        self.popen(cmd)

    def create_vm(self, total_num_vms):
        # form a shell command
        cmd = CMD_CREATE_VM.format(num_vms=total_num_vms)
        self.popen(cmd)
        return self.get_ip()

    def get_ip(self):
        """
        returns a list of ip
        """
        # form a shell command
        cmd = CMD_GET_IP
        result = self.popen(cmd).split("\n")
        return [x for x in result if x]

    def delete_vms(self):
        # form a shell command
        cmd = CMD_DELETE_VMS
        self.popen(cmd)

    def provision(self, ip_list, timeout=60):
        """
        :param ip_list: list of [..., [lang, ip], ...]
        :return:
        """
        _t_start = time.time()

        # TODO: log function
        print(f"PYSERVER::Minions::provision(): Provisioning the following langs: {[lang for lang, _ in ip_list]}")
        sys.stdout.flush()

        self.upload_ip_list(ip_list)

        # wait until ssh works fine
        ssh_ok = False
        while not ssh_ok:
            ssh_ok = True
            if time.time() - _t_start > timeout:
                raise TimeoutError("Couldn't run provision through ssh")
            try:
                for _, ip in ip_list:
                    # this one only checks if ssh working properly
                    self.popen(f"ssh stream@{ip} echo OK", num_retries=1)
            except Exception as ex:
                # if self.popen throws an exception, that means it couldn't run a command via ssh
                # try everything again
                logger.debug(f"Retry provision, failed with error: {ex}")
                ssh_ok = False
                time.sleep(2)
                continue

        # ./init.sh --upload-files
        #cmd = CMD_UPLOAD_FILES
        #self.popen(cmd)
        # ./init.sh --provision
        cmd = CMD_PROVISION
        self.popen(cmd)

    def check_provision(self, ip):
        """
        Checks provision status for given ip address (minion ip address).
        What the function does is it only checks the local `~/PROVISION_STATUS` file on a minion
        with an `ip` specified.
        :return: True/False
        """
        cmd = CMD_CHECK_PROVISION.format(ip=ip)
        status = self.popen(cmd)
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
        print("IP LIST: ", ip_list)
        placeholder = "\n".join([f"  [{lang}]={ip}" for lang, ip in ip_list])
        # read the template to form ip.list file
        with open(IP_LIST_EXAMPLE_PATH, "rt") as fp:
            template = fp.read()
        ip_list = template.format(placeholder=placeholder)
        with open("./ip.list", "wt") as fp:
            fp.write(ip_list)
        # upload ip.list file onto the server
        cmd = CMD_UPLOAD_IP_LIST.format(ip_list="./ip.list")
        self.popen(cmd)


@app.route("/minions/hcloud_context_list", methods=["GET"])
def hcloud_context_list():
    """
    Returns json of list.
    [... [[active], ip], ...] - where `active` is only filled if context is active. E.g.:
    [
     [context_1],
     [context_2],
     [*, context_3],
     [context_4]
    ]
    """
    result = cmd_context.hcloud_context_list()
    return ExecutionStatus(True, json.dumps(result)).to_http_status()


@app.route("/minions/hcloud_context_use", methods=["POST"])
def hcloud_context_use():
    """
    Selects a particular context to use
    """
    name = request.args.get("name", None)

    if not name:
        return ExecutionStatus(False, "Please specify `name`").to_http_status()

    cmd_context.hcloud_context_use(name)

    return ExecutionStatus(True, json.dumps("Ok")).to_http_status()


@app.route("/minions/get_ip", methods=["GET"])
def get_ip():
    """
    Returns an ip list
    """
    return ExecutionStatus(True, json.dumps(cmd_context.get_ip())).to_http_status()


@app.route("/minions/delete_vms", methods=["POST"])
def delete_vms():
    """
    Deletes all the servers
    """
    cmd_context.delete_vms()
    return ExecutionStatus(True, json.dumps("Ok")).to_http_status()


@app.route("/minions/provision", methods=["POST"])
def provision():
    """
    Has the following parameters:
    ip_list: list of [..., [lang, ip], ...]
    """
    ip_list = request.args.get("ip_list", None)

    if not ip_list:
        return ExecutionStatus(False, "Please specify `ip_list`").to_http_status()

    ip_list = json.loads(ip_list)
    cmd_context.provision(ip_list)

    return ExecutionStatus(True, json.dumps("Ok")).to_http_status()


@app.route("/minions/check_provision", methods=["GET"])
def check_provision():
    """
    Parameters:
    ip - minion ip address
    """
    ip = request.args.get("ip", None)

    if not ip:
        return ExecutionStatus(False, "Please specify `ip`").to_http_status()

    return ExecutionStatus(True, json.dumps(cmd_context.check_provision(ip))).to_http_status()


@app.route("/minions/create_vm", methods=["POST"])
def create_vm():
    """
    Parameters:
    count - total vms count
    """
    minions_count = request.args.get("count", None)

    if not minions_count:
        return ExecutionStatus(False, "Please specify `count`").to_http_status()

    minions_count = json.loads(minions_count)

    return ExecutionStatus(True, json.dumps(cmd_context.create_vm(minions_count))).to_http_status()


cmd_context = CMDContext()

if __name__ == "__main__":
    app.run("0.0.0.0", 9000)
