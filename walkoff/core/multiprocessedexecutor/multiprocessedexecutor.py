import logging
import multiprocessing
import os
import signal
import sys
import threading
import uuid

import gevent
import zmq.green as zmq

import walkoff.config.config
import walkoff.config.paths
from walkoff.events import WalkoffEvent
from walkoff.core.multiprocessedexecutor.loadbalancer import LoadBalancer, Receiver
from walkoff.core.multiprocessedexecutor.worker import Worker
from walkoff.core.multiprocessedexecutor.threadauthenticator import ThreadAuthenticator
from walkoff.coredb.workflow import Workflow
from walkoff.coredb.workflowresults import WorkflowStatus
from walkoff.coredb.saved_workflow import SavedWorkflow
from walkoff.coredb import WorkflowStatusEnum
import walkoff.coredb.devicedb

logger = logging.getLogger(__name__)

WORKFLOW_RUNNING = 1
WORKFLOW_PAUSED = 2
WORKFLOW_COMPLETED = 4
WORKFLOW_AWAITING_DATA = 5


def spawn_worker_processes(worker_environment_setup=None):
    """Initialize the multiprocessing pool, allowing for parallel execution of workflows.

    Args:
        worker_environment_setup (function, optional): Optional alternative worker setup environment function.
    """
    pids = []
    for i in range(walkoff.config.config.num_processes):
        args = (i, worker_environment_setup) if worker_environment_setup else (i,)

        pid = multiprocessing.Process(target=Worker, args=args)
        pid.start()
        pids.append(pid)
    return pids


class MultiprocessedExecutor(object):
    def __init__(self):
        """Initializes a multiprocessed executor, which will handle the execution of workflows.
        """
        self.threading_is_initialized = False
        self.id = "executor"
        self.pids = None
        self.workflows_executed = 0

        self.ctx = None
        self.auth = None

        self.manager = None
        self.manager_thread = None
        self.receiver = None
        self.receiver_thread = None

    def initialize_threading(self, pids):
        """Initialize the multiprocessing communication threads, allowing for parallel execution of workflows.

        """
        if not (os.path.exists(walkoff.config.paths.zmq_public_keys_path) and
                os.path.exists(walkoff.config.paths.zmq_private_keys_path)):
            logging.error("Certificates are missing - run generate_certificates.py script first.")
            sys.exit(0)
        self.pids = pids
        self.ctx = zmq.Context.instance()
        self.auth = ThreadAuthenticator(self.ctx)
        self.auth.start()
        self.auth.allow('127.0.0.1')
        self.auth.configure_curve(domain='*', location=walkoff.config.paths.zmq_public_keys_path)

        self.manager = LoadBalancer(self.ctx)
        self.receiver = Receiver(self.ctx)

        self.receiver_thread = threading.Thread(target=self.receiver.receive_results)
        self.receiver_thread.start()

        self.manager_thread = threading.Thread(target=self.manager.manage_workflows)
        self.manager_thread.start()

        self.threading_is_initialized = True
        logger.debug('Controller threading initialized')

    def wait_and_reset(self, num_workflows):
        timeout = 0
        shutdown = 10

        while timeout < shutdown:
            if self.receiver is not None and num_workflows == self.receiver.workflows_executed:
                break
            timeout += 0.1
            gevent.sleep(0.1)
        self.receiver.workflows_executed = 0

    def shutdown_pool(self):
        """Shuts down the threadpool.
        """
        self.manager.send_exit_to_worker_comms()
        if self.manager_thread:
            self.manager.thread_exit = True
            self.manager_thread.join(timeout=1)
        if len(self.pids) > 0:
            for p in self.pids:
                if p.is_alive():
                    os.kill(p.pid, signal.SIGABRT)
                    p.join(timeout=3)
                    try:
                        os.kill(p.pid, signal.SIGKILL)
                    except (OSError, AttributeError):
                        pass
        if self.receiver_thread:
            self.receiver.thread_exit = True
            self.receiver_thread.join(timeout=1)
        self.threading_is_initialized = False
        logger.debug('Controller thread pool shutdown')

        if self.auth:
            self.auth.stop()
        if self.ctx:
            self.ctx.destroy()
        self.cleanup_threading()
        return

    def cleanup_threading(self):
        """Once the threadpool has been shutdown, clear out all of the data structures used in the pool.
        """
        self.pids = []
        self.receiver_thread = None
        self.manager_thread = None
        self.workflows_executed = 0
        self.threading_is_initialized = False
        self.manager = None
        self.receiver = None

    def execute_workflow(self, workflow, start=None, start_arguments=None, resume=False):
        """Executes a workflow.

        Args:
            workflow (Workflow): The Workflow to be executed.
            start (str, optional): The ID of the first, or starting action. Defaults to None.
            start_arguments (list[Argument]): The arguments to the starting action of the workflow. Defaults to None.
            resume (bool, optional): Optional boolean to resume a previously paused workflow. Defaults to False.

        Returns:
            The execution ID of the Workflow.
        """
        if not resume:
            execution_id = str(uuid.uuid4())
            workflow._execution_id = execution_id
        else:
            execution_id = workflow.get_execution_id()

        if start is not None:
            logger.info('Executing workflow {0} for action {1}'.format(workflow.name, start))
        else:
            logger.info('Executing workflow {0} with default starting action'.format(workflow.name, start))

        print("Pending")
        WalkoffEvent.WorkflowExecutionPending.send(workflow)
        self.manager.add_workflow(workflow.id, execution_id, start, start_arguments, resume)

        WalkoffEvent.SchedulerJobExecuted.send(self)
        return execution_id

    def pause_workflow(self, execution_id):
        """Pauses a workflow that is currently executing.

        Args:
            execution_id (str): The execution id of the workflow.
        """
        workflow_status = walkoff.coredb.devicedb.device_db.session.query(WorkflowStatus).filter_by(
            execution_id=execution_id).first()
        print("Workflow status: {}".format(workflow_status.__dict__))
        if workflow_status and workflow_status.status == WorkflowStatusEnum.running:
            print("Sending pause message")
            self.manager.pause_workflow(execution_id)
            return True
        else:
            logger.warning('Cannot pause workflow {0}. Invalid key, or workflow not running.'.format(execution_id))
            return False

    def resume_workflow(self, execution_id):
        """Resumes a workflow that is currently paused.

        Args:
            execution_id (str): The execution id of the workflow.
        """
        print("In resume func")
        workflow_status = walkoff.coredb.devicedb.device_db.session.query(WorkflowStatus).filter_by(
            execution_id=execution_id).first()

        print(workflow_status.__dict__)

        if workflow_status and workflow_status.status == WorkflowStatusEnum.paused:
            print("Doing resume")
            saved_state = walkoff.coredb.devicedb.device_db.session.query(SavedWorkflow).filter_by(
                workflow_execution_id=execution_id).first()
            workflow = walkoff.coredb.devicedb.device_db.session.query(Workflow).filter_by(id=workflow_status.workflow_id).first()
            workflow._execution_id = execution_id
            WalkoffEvent.WorkflowResumed.send(workflow)
            self.execute_workflow(workflow, start=saved_state.action_id, resume=True)
            return True
        else:
            logger.warning('Cannot resume workflow {0}. Invalid key, or workflow not paused.'.format(execution_id))
            return False

    @staticmethod
    def get_waiting_workflows():
        """Gets a list of the execution IDs of workflows currently awaiting data to be sent to a trigger.

        Returns:
            A list of execution IDs of workflows currently awaiting data to be sent to a trigger.
        """
        wf_statuses = walkoff.coredb.devicedb.device_db.session.query(WorkflowStatus).filter_by(
            status=WorkflowStatusEnum.awaiting_data).all()
        return [str(wf_status.execution_id) for wf_status in wf_statuses]

    def get_workflow_status(self, execution_id):
        """Gets the current status of a workflow by its execution ID

        Args:
            execution_id (str): The execution ID of the workflow

        Returns:
            The status of the workflow
        """
        workflow_status = walkoff.coredb.devicedb.device_db.session.query(WorkflowStatus).filter_by(
            execution_id=execution_id).first()
        if workflow_status:
            return workflow_status.status
        else:
            logger.error("Key {} does not exist in database.").format(execution_id)
            return 0

    def send_data_to_trigger(self, data_in, workflow_execution_ids, arguments=None):
        """Sends the data_in to the workflows specified in workflow_execution_ids.

        Args:
            data_in (dict): Data to be used to match against the triggers for an Action awaiting data.
            workflow_execution_ids (list[str]): A list of workflow execution IDs to send this data to.
            arguments (list[Argument]): An optional list of Arguments to update for an
                Action awaiting data for a trigger. Defaults to None.
        """
        arguments = arguments if arguments is not None else []
        self.manager.send_data_to_trigger(data_in, workflow_execution_ids, arguments)
