class SourceSelector:
    def __init__(self):
        self.ip_list = ["127.0.0.1"]
        self.active_ip = "127.0.0.1"

    def set_ip_list(self, ip_list):
        self.ip_list = list(set(ip_list))

    def get_ip_list(self):
        return self.ip_list

    def set_active_ip(self, ip):
        if ip not in self.ip_list:
            raise KeyError("Invalid ip address")
        self.active_ip = ip

    def get_active_ip(self):
        return self.active_ip

    def dump_dict(self):
        return dict((
            (ip, ip == self.get_active_ip()) for ip in self.get_ip_list()
        ))
