class SourceSelector:
    def __init__(self):
        self.ip_list = []  # list of {"ip": "...", "label": "..."}
        self.active_ip = "*"

    def set_ip_list(self, ip_list):
        """
        :param ip_list: list of {"ip": "...", "label": "..."}
        :return:
        """
        assert isinstance(ip_list, list), f"`ip_list` should be type of list, {type(ip_list)} got"
        for element in ip_list:
            assert isinstance(element, dict), f"Element of `ip_list` should be type of dict, " \
                                              f"{type(element)} got"
            assert len(element) == 2 and \
                   "ip" in element and "label" in element, f"Element of `ip_list` should " \
                                                           f"have the following structure: " \
                                                           f"{{\"ip\": \"...\", \"label\": \"...\"}}, " \
                                                           f"got {element}"
        self.ip_list = ip_list
        self.set_active_ip("*")

    def get_ip_list(self):
        return self.ip_list

    def set_active_ip(self, ip):
        if ip not in [_["ip"] for _ in self.ip_list] and ip != "*":
            raise KeyError("Invalid ip address")
        self.active_ip = ip

    def get_active_ip(self):
        return self.active_ip

    def dump_dict(self):
        dump = [element.copy() for element in self.ip_list]
        for i in range(len(dump)):
            dump[i]["active"] = self.is_allowed(dump[i]["ip"])
        return dump

    def is_allowed(self, ip):
        active_ip = self.get_active_ip()
        if active_ip == "*":
            return True
        return active_ip == ip
