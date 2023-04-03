import json
import logging
import os
import sys
import time

from flask import Flask, request

from util.util import ExecutionStatus

app = Flask(__name__)
logger = logging.getLogger(__name__)

# CMD_HCLOUD_LIST = "hcloud context list"
# CMD_HCLOUD_USE = "hcloud context use {name}"
# CMD_CREATE_VM = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " \
#                 "./init.sh --cloud {cloud} --create-vm {num_vms}"
# CMD_GET_IP = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud {cloud} --getip"
# CMD_DELETE_VMS = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud {cloud} -d"
# CMD_UPLOAD_FILES = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --upload-files"
# CMD_PROVISION = "cd /home/user/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --provision"
# CMD_UPLOAD_IP_LIST = "cp {ip_list} /home/user/cloudobs-infrastructure-main/shared/scripts/ip.list"
# CMD_CHECK_PROVISION = "ssh -o StrictHostKeyChecking=no stream@{ip} cat /home/stream/PROVISION_STATUS"
# IP_LIST_EXAMPLE_PATH = "./ip.list.example"

CMD_HCLOUD_LIST = "hcloud context list"
CMD_HCLOUD_USE = "hcloud context use {name}"
CMD_CREATE_VM = "cd /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts && " \
                "./init.sh --cloud {cloud} --create-vm {num_vms}"
CMD_GET_IP = "cd /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud {cloud} --getip"
CMD_DELETE_VMS = "cd /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --cloud {cloud} -d"
CMD_UPLOAD_FILES = "cd /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --upload-files"
CMD_PROVISION = "cd /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts && " "./init.sh --provision"
CMD_UPLOAD_IP_LIST = "cp {ip_list} /Users/amukhsimov/temp/cloudobs-infrastructure-main/shared/scripts/ip.list"
CMD_CHECK_PROVISION = "ssh -o StrictHostKeyChecking=no stream@{ip} cat /home/stream/PROVISION_STATUS"
IP_LIST_EXAMPLE_PATH = "./ip.list.example"


class MinionsService:
    class CMDContext:
        def __init__(self):
            pass

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

    class Command:
        def __init__(self, service):
            self.service: MinionsService = service

        def exec(self, command, details=None):
            if command == "create_vm":
                # details: {"vms": value}
                # where 'value' is either integer or list:
                # [['hetzner', 'hetzner_account_1', 10], ['hetzner', 'hetzner_account_2', 5]]
                # if only an integer passed as 'value' then service deploys machines using
                # only the active hetzner account
                if details in None or not isinstance(details, dict) or "vms" not in details:
                    return ExecutionStatus(False, f"Invalid arguments passed for command '{command}'")

                return self.create_vm(vms=details["vms"])
            elif command == "getip":
                pass

        def hcloud_context_list(self) -> ExecutionStatus:
            # form a shell command
            cmd = CMD_HCLOUD_LIST
            result = self.popen(cmd, num_retries=0)
            # if none returned, mostly that means we couldn't establish connection
            if not result:
                return ExecutionStatus(False, "Couldn't execute `hcloud context list` command.")
            #
            lines = result.serializable_object.split("\n")
            if len(lines) <= 1:
                return ExecutionStatus(False, "No context found for hetzner")
            # `hcloud context list` prints out two columns of data (ACTIVE MAME)
            # skip first entry since this is a header
            lines = [line.split() for line in lines][1:]
            lines = [[len(line) == 2, line[-1]] for line in lines if line]  # len(line) == 2 => that means ACTIVE context
            # returns a list of [... [[active], ip], ...] -- `active` variable is only filled in active ip
            return ExecutionStatus(True, serializable_object=lines)

        def hcloud_get_active_context(self) -> ExecutionStatus:
            contexts = self.hcloud_context_list()
            if not contexts:
                raise RuntimeError("No contexts found")
            for active, context in contexts.serializable_object:
                if active:
                    return context
            raise RuntimeError("No contexts found")

        def hcloud_context_use(self, context):
            # form a shell command
            cmd = CMD_HCLOUD_USE.format(name=context)
            self.popen(cmd, num_retries=0)

        def hetzner_getip(self, context=None) -> ExecutionStatus:
            """
            Returns ips in the following format:
            [
              [ 'hetzner', 'context1', [ ip1, ip2, ..., ipn ] ],
              [ 'hetzner', 'context2', [ ip1, ip2, ..., ipn ] ],
              ...
            ]
            """
            # if context is specified
            if context is not None:
                contexts = [[True, context]]
            # if context is not specified, get ip from all available contexts
            else:
                result = self.hcloud_context_list()  # list contexts
                if not result:
                    return result
                contexts = result.serializable_object

            context_ips = []
            # save active context
            active_context = self.hcloud_get_active_context()

            for _, context in contexts:  # for every context
                self.hcloud_context_use(context)  # activate it
                cmd = CMD_GET_IP.format(cloud="hetzner")
                result = self.popen(cmd, num_retries=0)  # getip
                if result:  # if the command executed successfully -> append it to result
                    context_ips.append(["hetzner", context, result.serializable_object.split("\n")])

            self.hcloud_context_use(active_context)  # return active context
            return ExecutionStatus(True, serializable_object=context_ips)

        def get_ip(self, cloud=None, context=None):
            """
            Executes getip command for a context. If context is not specified, executes it for all contexts.
            Returns ips in the following format:
            [
              [ 'cloud1', 'context1', [ ip1, ip2, ..., ipn ] ],
              [ 'cloud2', 'context2', [ ip1, ip2, ..., ipn ] ],
              ...
            ]
            """
            return self.hetzner_getip(context=context)

        def hetzner_delete_minions(self, context=None) -> ExecutionStatus:
            # if context is not specified use the active one
            if context is None:
                context = self.hcloud_get_active_context()

            # save active context
            active_context = self.hcloud_get_active_context()

            self.hcloud_context_use(context)  # activate it
            cmd = CMD_DELETE_VMS.format(cloud="hetzner")
            self.popen(cmd, num_retries=0)

            self.hcloud_context_use(active_context)  # return active context
            return ExecutionStatus(True)

        def delete_vms(self, cloud, context=None) -> ExecutionStatus:
            if cloud == "hetzner":
                return self.hetzner_delete_minions(context=context)
            else:
                return ExecutionStatus(False, f"Unsupported cloud '{cloud}'")

        def create_vm(self, vms) -> ExecutionStatus:
            """
            'vms' can be either integer or list.
            If 'vms' is an integer -> service deploys minions using active context.
            If 'vms' is a list, it should have the following structure:
            [
              [ 'cloud1', 'account1', num_vms ],
              [ 'cloud1', 'account2', num_vms ],
              [ 'cloud2', 'account1', num_vms ],
              ...
            ]
            """
            if isinstance(vms, int):
                return self.popen(CMD_CREATE_VM.format(cloud="hetzner", num_vms=vms), num_retries=0)
            elif isinstance(vms, list):
                # validate vms structure
                if not all([isinstance(v, list) and len(v) == 3 for v in vms]) or \
                    not all([isinstance(num_vms, int) and isinstance(cloud, str) and isinstance(account, str)
                             for cloud, account, num_vms in vms]):
                    return ExecutionStatus(False, f"Invalid 'vms' format")
                # save last active context
                active_context = self.hcloud_get_active_context()
                for cloud, account, num_vms in vms:
                    if cloud == "hetzner":
                        self.hcloud_context_use(account)  # activate context

                        cmd = CMD_CREATE_VM.format(cloud="hetzner", num_vms=num_vms)
                        result = self.popen(cmd, num_retries=0)  # create vms
                        ips = self.get_ip(cloud="hetzner", context=account)
                        if not result or not ips or not ips.serializable_object \
                                or len(ips.serializable_object[0][2]) < num_vms:  # if couldn't deploy minions
                            for cloud, account, _ in vms:
                                self.delete_vms(cloud=cloud, context=account)  # delete all deployed minions
                            self.hcloud_context_use(active_context)
                            return ExecutionStatus(False, f"Couldn't deploy enough minions using context {account}")
                    else:
                        for cloud, account, _ in vms:
                            self.delete_vms(cloud=cloud, context=account)  # delete all deployed minions
                        return ExecutionStatus(False, f"Cloud '{cloud}' is not supported yet")

                self.hcloud_context_use(active_context)
                return ExecutionStatus(True)
            else:
                return ExecutionStatus(False, f"Invalid arguments passed for command 'create_vm'")

        def popen(self, cmd, num_retries=0) -> ExecutionStatus:
            count_attempts = 0

            while count_attempts <= num_retries:
                with os.popen(cmd) as fp:  # run command
                    result = fp.read()
                    return_code = fp.close()
                    if return_code is None:
                        return_code = 0
                    count_attempts += 1

                    if return_code == 0:
                        return ExecutionStatus(True, serializable_object=result)

                    time.sleep(5)
            return ExecutionStatus(False, f"Cannot execute command: '{cmd}'")

    def __init__(self):
        self.command = MinionsService.Command(self)


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
