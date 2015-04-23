# -*- coding: utf-8 -*-
from collections import defaultdict
from collections import namedtuple
import logging
import signal
import sys

from pymysqlreplication.event import QueryEvent
from pymysqlreplication.row_event import UpdateRowsEvent
from pymysqlreplication.row_event import WriteRowsEvent

from yelp_batch import Batch
from replication_handler.components.binlogevent_yielder import BinlogEventYielder
from replication_handler.components.data_event_handler import DataEventHandler
from replication_handler.components.schema_event_handler import SchemaEventHandler
from replication_handler.components.stubs.stub_dp_clientlib import DPClientlib
from replication_handler.models.database import rbr_state_session
from replication_handler.models.data_event_checkpoint import DataEventCheckpoint
from replication_handler.models.global_event_state import EventType
from replication_handler.models.global_event_state import GlobalEventState

log = logging.getLogger('replication_handler.batch.parse_replication_stream')

HandlerInfo = namedtuple("HandlerInfo", ("event_type", "handler"))


class ParseReplicationStream(Batch):
    """Batch that follows the replication stream and continuously publishes
       to kafka.
       This involves
       (1) Using python-mysql-replication to get stream events.
       (2) Calls to the schema store to get the avro schema
       (3) Avro serialization of the payload.
       (4) Publishing to kafka through a datapipeline clientlib
           that will encapsulate payloads.
    """
    notify_emails = ['bam+batch@yelp.com']
    current_event_type = None

    def __init__(self):
        super(ParseReplicationStream, self).__init__()

        self.dp_client = DPClientlib()
        self.binlog_event_yielder = BinlogEventYielder()
        data_event_handler = DataEventHandler()
        schema_event_handler = SchemaEventHandler()

        self.handler_map = defaultdict()
        self.handler_map[WriteRowsEvent] = HandlerInfo(
            event_type=EventType.DATA_EVENT,
            handler=data_event_handler
        )
        self.handler_map[UpdateRowsEvent] = HandlerInfo(
            event_type=EventType.DATA_EVENT,
            handler=data_event_handler
        )
        self.handler_map[QueryEvent] = HandlerInfo(
            event_type=EventType.SCHEMA_EVENT,
            handler=schema_event_handler
        )

        # Register the handler for SIGINT(KeyboardInterrupt) and SigTerm
        signal.signal(signal.SIGINT, self._handle_graceful_termination)
        signal.signal(signal.SIGTERM, self._handle_graceful_termination)

    def run(self):
        for replication_handler_event in self.binlog_event_yielder:
            event_class = replication_handler_event.event.__class__
            self.current_event_type = self.handler_map[event_class]
            self.handler_map[event_class].handler.handle_event(
                replication_handler_event.event,
                replication_handler_event.gtid
            )

    def _handle_graceful_termination(self, signal, frame):
        """This function would be invoked when SIGINT and SIGTERM
        signals are fired.
        """
        # We will not do anything for SchemaEvent, because we have
        # a good way to recover it.
        if self.current_event_type == EventType.DATA_EVENT:
            self.dp_client.flush()
            offset_info = self.dp_client.get_latest_published_offset()
            with rbr_state_session.connect_begin(ro=False) as session:
                DataEventCheckpoint.create_data_event_checkpoint(
                    session=session,
                    gtid=offset_info.gtid,
                    offset=offset_info.offset,
                    table_name=offset_info.table_name
                )
                GlobalEventState.upsert(
                    session=session,
                    gtid=offset_info.gtid,
                    event_type=EventType.DATA_EVENT,
                    is_clean_shutdown=True
                )
        log.info("Gracefully shutting down.")
        sys.exit()


if __name__ == '__main__':
    ParseReplicationStream().start()
