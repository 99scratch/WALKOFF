import json
from unittest import TestCase
from uuid import uuid4

from mock import patch
from zmq import Socket

from tests.util import initialize_test_config
from tests.util.execution_db_help import setup_dbs
from tests.util.mock_objects import MockRedisCacheAdapter
from walkoff.executiondb.argument import Argument
from walkoff.multiprocessedexecutor.protoconverter import ExecuteWorkflowMessage, CommunicationPacket, WorkflowControl, \
    ProtobufWorkflowCommunicationConverter, ProtobufWorkflowResultsConverter
from walkoff.multiprocessedexecutor.zmq_senders import ZmqWorkflowCommunicationSender
from walkoff.proto.build.data_pb2 import Message


class TestWorkflowCommunicationSender(TestCase):

    @classmethod
    def setUpClass(cls):
        initialize_test_config()
        cls.cache = MockRedisCacheAdapter()
        cls.controller = ZmqWorkflowCommunicationSender()
        setup_dbs()

    def tearDown(self):
        self.cache.clear()

    @classmethod
    def tearDownClass(cls):
        cls.controller.comm_socket.close()

    @staticmethod
    def assert_message_sent(mock_send, expected_message):
        mock_send.assert_called_once()
        mock_send.assert_called_with(expected_message)

    @patch.object(Socket, 'send')
    def test_send_message(self, mock_send):
        self.controller._send_message(Message().SerializeToString())
        self.assert_message_sent(mock_send, Message().SerializeToString())

    @patch.object(Socket, 'send')
    def test_send_exit_to_worker_comms(self, mock_send):
        self.controller.send_exit_to_workers()
        expected_message = CommunicationPacket()
        expected_message.type = CommunicationPacket.EXIT
        expected_message = expected_message.SerializeToString()
        self.assert_message_sent(mock_send, expected_message)

    def test_create_workflow_control_message(self):
        uid = str(uuid4())
        message = ProtobufWorkflowCommunicationConverter._create_workflow_control_message(WorkflowControl.PAUSE, uid)
        expected_message = CommunicationPacket()
        expected_message.ParseFromString(message)
        self.assertEqual(expected_message.workflow_control_message.type, WorkflowControl.PAUSE)
        self.assertEqual(expected_message.workflow_control_message.workflow_execution_id, uid)

    @patch.object(Socket, 'send')
    def test_abort_workflow(self, mock_send):
        uid = str(uuid4())
        message = ProtobufWorkflowCommunicationConverter._create_workflow_control_message(WorkflowControl.ABORT, uid)
        self.controller.abort_workflow(uid)
        self.assert_message_sent(mock_send, message)

    @patch.object(Socket, 'send')
    def test_pause_workflow(self, mock_send):
        uid = str(uuid4())
        message = ProtobufWorkflowCommunicationConverter._create_workflow_control_message(WorkflowControl.PAUSE, uid)
        self.controller.pause_workflow(uid)
        self.assert_message_sent(mock_send, message)

    def test_set_argumets_for_proto(self):
        message = ExecuteWorkflowMessage()
        uid = uuid4()
        selection = [Argument('test', 1), Argument('test', 'a'), Argument('test', '32'), Argument('test', 46)]
        arguments = [
            Argument('name1', value=32), Argument('name2', reference=uid, selection=selection)]
        ProtobufWorkflowResultsConverter._add_arguments_to_proto(message.arguments, arguments)
        self.assertEqual(len(message.arguments), len(arguments))
        self.assertEqual(message.arguments[0].name, arguments[0].name)
        self.assertEqual(message.arguments[0].value, str(arguments[0].value))
        self.assertEqual(len(message.arguments[0].selection), 0)

        self.assertEqual(message.arguments[1].name, arguments[1].name)
        self.assertEqual(message.arguments[1].value, '')
        self.assertEqual(message.arguments[1].reference, str(uid))
        self.assertEqual(len(message.arguments[1].selection), len(selection))
