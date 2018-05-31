import logging
from uuid import UUID

from sqlalchemy import Column, String, ForeignKey, orm, UniqueConstraint, Boolean, event
from sqlalchemy.orm import relationship
from sqlalchemy_utils import UUIDType

from walkoff.appgateway.appinstancerepo import AppInstanceRepo
from walkoff.events import WalkoffEvent
from walkoff.executiondb import Execution_Base
from walkoff.executiondb.action import Action
from walkoff.executiondb.childworkflows import ChildWorkflow
from walkoff.executiondb.executionelement import ExecutionElement

logger = logging.getLogger(__name__)


class Workflow(ExecutionElement, Execution_Base):
    __tablename__ = 'workflow'
    playbook_id = Column(UUIDType(binary=False), ForeignKey('playbook.id'))
    name = Column(String(80), nullable=False)
    actions = relationship('Action', cascade='all, delete-orphan')
    branches = relationship('Branch', cascade='all, delete-orphan')
    child_workflows = relationship('ChildWorkflow', cascade='all, delete-orphan')
    start = Column(UUIDType(binary=False))
    is_valid = Column(Boolean, default=False)
    children = ('actions', 'branches')
    __table_args__ = (UniqueConstraint('playbook_id', 'name', name='_playbook_workflow'),)

    def __init__(self, name, start, id=None, actions=None, branches=None):
        """Initializes a Workflow object. A Workflow falls under a Playbook, and has many associated Actions
            within it that get executed.

        Args:
            name (str): The name of the Workflow object.
            start (int): ID of the starting Action.
            id (str|UUID, optional): Optional UUID to pass into the Action. Must be UUID object or valid UUID string.
                Defaults to None.
            actions (list[Action]): Optional Action objects. Defaults to None.
            branches (list[Branch], optional): A list of Branch objects for the Workflow object. Defaults to None.
        """
        ExecutionElement.__init__(self, id)
        self.name = name
        self.actions = actions if actions else []
        self.branches = branches if branches else []

        self.start = start

        self._is_paused = False
        self._abort = False
        self._accumulator = {branch.id: 0 for branch in self.branches}
        self._execution_id = 'default'
        self._instance_repo = None

        self.validate()

    @orm.reconstructor
    def init_on_load(self):
        """Loads all necessary fields upon Workflow being loaded from database"""
        self._is_paused = False
        self._abort = False
        self._accumulator = {branch.id: 0 for branch in self.branches}
        self._instance_repo = AppInstanceRepo()
        self._execution_id = 'default'

    def validate(self):
        """Validates the object"""
        action_ids = [action.id for action in self.actions]
        errors = []
        if not self.start and self.actions:
            errors.append('Workflows with actions require a start parameter')
        elif self.actions and self.start not in action_ids:
            errors.append('Workflow start ID {} not found in actions'.format(self.start))
        for branch in self.branches:
            if branch.source_id not in action_ids:
                errors.append('Branch source ID {} not found in workflow actions'.format(branch.source_id))
            if branch.destination_id not in action_ids:
                errors.append('Branch destination ID {} not found in workflow actions'.format(branch.destination_id))
        self.errors = errors
        self.is_valid = self._is_valid

    def get_action_by_id(self, action_id):
        """Gets an Action by its ID

        Args:
            action_id (UUID): The ID of the Action to find

        Returns:
            (Action): The Action from its ID
        """
        return next((action for action in self.actions if action.id == action_id), None)

    def get_child_workflow_by_id(self, workflow_id):
        """Gets an Child Workflow by its ID

        Args:
            workflow_id (UUID): The ID of the Workflow to find

        Returns:
            (ChildWorkflow): The ChildWorkflow from its ID
        """
        return next((workflow for workflow in self.child_workflows if workflow.id == workflow_id), None)

    def remove_action(self, action_id):
        """Removes a Action object from the Workflow's list of Actions given the Action ID.

        Args:
            action_id (UUID): The ID of the Action object to be removed.

        Returns:
            (bool): True on success, False otherwise.
        """
        action_to_remove = self.get_action_by_id(action_id)
        self.actions.remove(action_to_remove)
        self.branches[:] = [branch for branch in self.branches if
                            (branch.source_id != action_id and branch.destination_id != action_id)]

        logger.debug('Removed action {0} from workflow {1}'.format(action_id, self.name))
        return True

    def pause(self):
        """Pauses the execution of the Workflow. The Workflow will pause execution before starting the next Action"""
        self._is_paused = True
        logger.info('Pausing workflow {0}'.format(self.name))

    def abort(self):
        """Aborts the execution of the Workflow. The Workflow will abort execution before starting the next Action"""
        self._abort = True
        logger.info('Aborting workflow {0}'.format(self.name))

    def execute(self, execution_id, start=None, start_arguments=None, resume=False):
        """Executes a Workflow by executing all Actions in the Workflow list of Action objects.

        Args:
            execution_id (UUID): The UUID4 hex string uniquely identifying this workflow instance
            start (int, optional): The ID of the first Action. Defaults to None.
            start_arguments (list[Argument]): Argument parameters into the first Action. Defaults to None.
            resume (bool, optional): Optional boolean to resume a previously paused workflow. Defaults to False.
        """
        if self.is_valid:
            self._execution_id = execution_id
            logger.info('Executing workflow {0}'.format(self.name))
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.WorkflowExecutionStart)
            start = start if start is not None else self.start
            if not isinstance(start, UUID):
                start = UUID(start)
            executor = self.__execute(start, start_arguments, resume)
            workflow_result = next(executor)
            return workflow_result
        else:
            logger.error('Workflow is invalid, yet executor attempted to execute.')

    def __execute(self, start, start_arguments=None, resume=False):
        actions = self.__actions(start=start)
        last_result = None
        for action in (action_ for action_ in actions if action_ is not None):
            self._executing_action = action
            logger.debug('Executing action {0} of workflow {1}'.format(action, self.name))

            if self._is_paused:
                self._is_paused = False
                WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.WorkflowPaused)
                logger.debug('Paused workflow {} (id={})'.format(self.name, str(self.id)))
                yield 'Paused'
            if self._abort:
                self._abort = False
                WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.WorkflowAborted)
                logger.info('Aborted workflow {} (id={})'.format(self.name, str(self.id)))
                yield 'Aborted'

            device_id = self._instance_repo.setup_app_instance(action, self)
            action_args = {'arguments': start_arguments, 'resume': resume}
            if device_id:
                action_args['instance'] = self._instance_repo.get_app_instance(device_id)()
            result = action.execute(self._accumulator, **action_args)

            if start_arguments:
                start_arguments = None

            if result and result.status == "trigger":
                yield 'Triggered'
            last_result = action.get_output().result
            self._accumulator[action.id] = action.get_output().result
        self.__shutdown()
        yield last_result

    def __actions(self, start):
        next_executable_id = start
        next_executable = self.get_action_by_id(next_executable_id)
        if next_executable is None:
            next_executable = self.get_child_workflow_by_id(next_executable_id)

        while next_executable:
            yield next_executable
            next_executable_id, next_executable_type = self.get_branch(next_executable, self._accumulator)
            if next_executable_id is None:
                next_executable = None
            elif next_executable_type == 'action':
                next_executable = self.get_action_by_id(next_executable_id)
            else:
                next_executable = self.get_child_workflow_by_id(next_executable_id)
            yield  # needed so that when for-loop calls next() it doesn't advance too far
        yield  # needed so you can avoid catching StopIteration exception

    def get_branch(self, current_action, accumulator):
        """Executes the Branch objects associated with this Workflow to determine which Action should be
            executed next.

        Args:
            current_action(Action): The current action that has just finished executing.
            accumulator (dict): The accumulated results of previous Actions.

        Returns:
            (UUID): The ID of the next Action to be executed if successful, else None.
        """
        if self.branches:
            branches = sorted(
                self.__get_branches_by_action_id(current_action.id), key=lambda branch_: branch_.priority)
            for branch in branches:
                # TODO: This here is the only hold up from getting rid of action._output.
                # Keep whole result in accumulator
                destination_id, destination_type = branch.execute(current_action.get_output(), accumulator)
                if destination_id is not None:
                    logger.debug('Branch {} with destination {} chosen by workflow {} (id={})'.format(
                        str(branch.id), str(destination_id), self.name, str(self.id)))
                    return destination_id, destination_type
            return None, None
        else:
            return None, None

    def __get_branches_by_action_id(self, id_):
        branches = []
        if self.branches:
            for branch in self.branches:
                if branch.source_id == id_:
                    branches.append(branch)
        return branches

    def __shutdown(self):
        # Upon finishing shut down instances
        self._instance_repo.shutdown_instances()
        accumulator = {str(key): value for key, value in self._accumulator.items()}
        WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.WorkflowShutdown, data=accumulator)
        logger.info('Workflow {0} completed. Result: {1}'.format(self.name, self._accumulator))

    def set_execution_id(self, execution_id):
        """Sets the execution UUIDD for the Workflow

        Args:
            execution_id (UUID): The execution ID
        """
        self._execution_id = execution_id

    def get_execution_id(self):
        """Gets the execution ID for the Workflow

        Returns:
            (UUID): The execution ID of the Workflow
        """
        return self._execution_id

    def get_executing_action_id(self):
        """Gets the ID of the currently executing Action

        Returns:
            (UUID): The ID of the currently executing Action
        """
        return self._executing_action.id

    def get_executing_action(self):
        """Gets the currently executing Action

        Returns:
            (Action): The currently executing Action
        """
        return self._executing_action

    def get_accumulator(self):
        """Gets the accumulator

        Returns:
            (dict): The accumulator
        """
        return self._accumulator

    def get_instances(self):
        """Gets all instances

        Returns:
            (list[AppInstance]): All instances
        """
        return self._instance_repo.get_all_app_instances()


@event.listens_for(Workflow, 'before_update')
def validate_before_update(mapper, connection, target):
    target.validate()
