import asyncio
import dataclasses
import logging
import pprint
import uuid
from typing import Optional

from ephyr_control.instance import RemoteEphyrInstance, Subscription, queries as gql_operations


@dataclasses.dataclass
class SourceWatcher:
    instance: RemoteEphyrInstance
    subscription: Subscription
    restream_id: Optional[uuid.UUID]
    last_source: Optional[str] = None

    connection_broke: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)

    async def run_subscription(self):
        async with self.subscription.session() as sub_session:
            async for updated_state in sub_session.iterate():
                if self.connection_broke.is_set():
                    break
                try:
                    self.check_updated_state(updated_state)
                except Exception as exc:
                    logging.exception(f'Unexpected error caused by handling update data: {exc}')

    def get_foi_data(self, state_data: dict) -> Optional[list[dict]]:
        """
        return data of failover inputs
        :param state_data:
        :return:
        """
        restreams = state_data['allRestreams']
        for r in restreams:
            if r['id'] == str(self.restream_id):
                return r['input']['src']['inputs']
        else:
            return None

    def get_online_input_data(self, failover_inputs_data: list[dict]) -> Optional[dict]:
        """
        Return key of the streaming ONLINE input
        :param failover_inputs_data:
        :return:
        """
        for foi_data in failover_inputs_data:
            if any(status == 'ONLINE' for status in (endpoint['status'] for endpoint in foi_data['endpoints'])):
                return foi_data
        else:
            return None

    def check_source_changed(self, online_failover_input_data: dict):
        if online_failover_input_data['key'] != self.last_source:
            self.last_source = online_failover_input_data['key']

            label = None
            for endpoint in online_failover_input_data['endpoints']:
                if endpoint['status'] == 'ONLINE':
                    label = endpoint['label']
            if label is None:
                pprint.pprint(online_failover_input_data)

            logging.info(f'Source changed: {self.last_source} "{label if label else ""}"')

    def check_updated_state(self, updated_state: dict):
        failover_inputs_data = self.get_foi_data(updated_state)
        if failover_inputs_data is None:
            logging.warning(f'Restream not found')
            return

        online_foi_data = self.get_online_input_data(failover_inputs_data)
        if online_foi_data is None:
            logging.warning(f'No online inputs!')
            # TODO: what to do in this case?
        else:
            self.check_source_changed(online_foi_data)


def main():
    inst = RemoteEphyrInstance(ipv4='142.132.160.159', https=False)
    watcher = SourceWatcher(
        instance=inst,
        subscription=Subscription(inst, gql_operations.api_subscribe_to_state),
        restream_id=uuid.UUID('32b827d1-8d56-469d-8161-9d877e280145'),
    )
    asyncio.run(watcher.run_subscription())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.getLogger('gql.transport.websockets').setLevel(logging.WARNING)

    main()
