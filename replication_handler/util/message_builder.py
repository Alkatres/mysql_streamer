# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

import logging

from pii_generator.components.pii_identifier import PIIIdentifier
from data_pipeline.message import UpdateMessage

from replication_handler.config import env_config


log = logging.getLogger('replication_handler.parse_replication_stream')


class MessageBuilder(object):
    """ This class knows how to convert a data event into a respective message.

    Args:
      event(ReplicationHandlerEveent object): contains a create/update/delete data event and its position.
      schema_info(SchemaInfo object): contain topic/schema_id.
      resgiter_dry_run(boolean): whether a schema has to be registered for a message to be published.
    """
    def __init__(self, schema_info, event, position, register_dry_run=True):
        self.schema_info = schema_info
        self.event = event
        self.position = position
        self.register_dry_run = register_dry_run
        self.pii_identifier = PIIIdentifier(env_config.pii_yaml_path)

    def build_message(self):
        message_params = {
            "topic": self.schema_info.topic,
            "schema_id": self.schema_info.schema_id,
            "keys": tuple([unicode(x) for x in self.schema_info.primary_keys]),
            "payload_data": self._get_values(self.event.row),
            "upstream_position_info": self.position.to_dict(),
            "dry_run": self.register_dry_run,
            "contains_pii": self.pii_identifier.table_has_pii(
                database_name=self.event.schema,
                table_name=self.event.table
            ),
            "transaction_id": self.position.get_transaction_id(),
        }

        if self.event.message_type == UpdateMessage:
            message_params["previous_payload_data"] = self.event.row["before_values"]

        return self.event.message_type(**message_params)

    def _get_values(self, row):
        """Gets the new value of the row changed.  If add row occurs,
           row['values'] contains the data.
           If an update row occurs, row['after_values'] contains the data.
        """
        if 'values' in row:
            return row['values']
        elif 'after_values' in row:
            return row['after_values']
