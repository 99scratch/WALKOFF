import logging

import gevent
import zmq.green as zmq
from quart import Quart

import walkoff.config
from walkoff.events import WalkoffEvent
from walkoff.multiprocessedexecutor.protoconverter import ProtobufWorkflowResultsConverter
from walkoff.server import context

logger = logging.getLogger(__name__)


class ZmqWorkflowResultsReceiver(object):
    def __init__(self, message_converter=ProtobufWorkflowResultsConverter, current_app=None):
        """Initialize a Receiver object, which will receive callbacks from the ExecutionElements.

        Args:
            current_app (Quart.App, optional): The current Quart app. If the Receiver is not started separately,
                then the current_app must be included in the init. Otherwise, it should not be included.
            message_converter (WorkflowResultsConverter): Class to convert workflow results
        """
        import walkoff.server.workflowresults  # Need this import

        ctx = zmq.Context.instance()
        self.message_converter = message_converter
        self.thread_exit = False
        self.workflows_executed = 0

        self.results_sock = ctx.socket(zmq.PULL)
        self.results_sock.curve_secretkey = walkoff.config.Config.SERVER_PRIVATE_KEY
        self.results_sock.curve_publickey = walkoff.config.Config.SERVER_PUBLIC_KEY
        self.results_sock.curve_server = True
        self.results_sock.bind(walkoff.config.Config.ZMQ_RESULTS_ADDRESS)

        if current_app is None:
            self.current_app = Quart(__name__)
            self.current_app.config.from_object(walkoff.config.Config)
            self.current_app.running_context = context.Context(init_all=False)
        else:
            self.current_app = current_app

    def receive_results(self):
        """Keep receiving results from execution elements over a ZMQ socket, and trigger the callbacks"""
        while True:
            if self.thread_exit:
                break
            try:
                message_bytes = self.results_sock.recv(zmq.NOBLOCK)
            except zmq.ZMQError:
                gevent.sleep(0.1)
                continue

            with self.current_app.app_context():
                self._send_callback(message_bytes)

        self.results_sock.close()
        return

    def _send_callback(self, message_bytes):
        event, sender, data = self.message_converter.to_event_callback(message_bytes)

        if sender is not None and event is not None:
            if self.current_app:
                with self.current_app.app_context():
                    event.send(sender, data=data)
            else:
                event.send(sender, data=data)
            if event in [WalkoffEvent.WorkflowShutdown, WalkoffEvent.WorkflowAborted]:
                self._increment_execution_count()

    def _increment_execution_count(self):
        self.workflows_executed += 1
