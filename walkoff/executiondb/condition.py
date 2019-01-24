import logging

from sqlalchemy import Column, ForeignKey, String, orm, Boolean, event
from sqlalchemy.orm import relationship
from sqlalchemy_utils import UUIDType

from walkoff import executiondb
from walkoff.appgateway import get_condition
from walkoff.appgateway.apiutil import split_api_params, get_condition_api, UnknownApp, InvalidArgument, \
    UnknownCondition
from walkoff.appgateway.validator import validate_condition_parameters
from walkoff.events import WalkoffEvent
from walkoff.executiondb.argument import Argument
from walkoff.executiondb.executionelement import ExecutionElement
from walkoff.helpers import ExecutionError, format_exception_message

logger = logging.getLogger(__name__)


class Condition(ExecutionElement, executiondb.Execution_Base):
    __tablename__ = 'condition'
    conditional_expression_id = Column(UUIDType(binary=False),
                                       ForeignKey('conditional_expression.id', ondelete='CASCADE'))
    app_name = Column(String(80), nullable=False)
    action_name = Column(String(80), nullable=False)
    is_negated = Column(Boolean, default=False)
    arguments = relationship('Argument', cascade='all, delete, delete-orphan', passive_deletes=True)
    transforms = relationship('Transform', cascade='all, delete-orphan', passive_deletes=True)
    children = ('arguments', 'transforms')

    def __init__(self, app_name, action_name, id=None, is_negated=False, arguments=None, transforms=None, errors=None):
        """Initializes a new Condition object.

        Args:
            app_name (str): The name of the app which contains this condition
            action_name (str): The action name for the Condition. Defaults to an empty string.
            id (str|UUID, optional): Optional UUID to pass into the Condition. Must be UUID object or valid UUID string.
                Defaults to None.
            is_negated (bool, optional): Should the result of the condition be inverted? Defaults to False.
            arguments (list[Argument], optional): Dictionary of Argument keys to Argument values.
                This dictionary will be converted to a dictionary of str:Argument. Defaults to None.
            transforms(list[Transform], optional): A list of Transform objects for the Condition object.
                Defaults to None.
        """
        ExecutionElement.__init__(self, id, errors)
        self.app_name = app_name
        self.action_name = action_name
        self.is_negated = is_negated

        self.arguments = []
        if arguments:
            self.arguments = arguments

        self.transforms = []
        if transforms:
            self.transforms = transforms

        self._data_param_name = None
        self._api = None
        self._condition_executable = None

        self.validate()

    @orm.reconstructor
    def init_on_load(self):
        """Loads all necessary fields upon Condition being loaded from database"""
        if not self.errors:
            errors = []
            try:
                self._data_param_name, run, self._api = get_condition_api(self.app_name, self.action_name)
                get_condition(self.app_name, run)
            except UnknownApp:
                errors.append('Unknown app {}'.format(self.app_name))
            except UnknownCondition:
                errors.append('Unknown condition {}'.format(self.action_name))
            self.errors = errors

    def validate(self):
        """Validates the object"""
        errors = []
        try:
            self._data_param_name, run, self._api = get_condition_api(self.app_name, self.action_name)
            self._condition_executable = get_condition(self.app_name, run)
            tmp_api = split_api_params(self._api, self._data_param_name)
            validate_condition_parameters(tmp_api, self.arguments, self.action_name)
        except UnknownApp:
            errors.append('Unknown app {}'.format(self.app_name))
        except UnknownCondition:
            errors.append('Unknown condition {}'.format(self.action_name))
        except InvalidArgument as e:
            errors.extend(e.errors)
        self.errors = errors

    def execute(self, action_execution_strategy, data_in, accumulator):
        """Executes the Condition object, determining if the Condition evaluates to True or False.

        Args:
            action_execution_strategy: The strategy used to execute the action (e.g. LocalActionExecutionStrategy)
            data_in (dict): The input to the Transform objects associated with this Condition.
            accumulator (dict): The accumulated data from previous Actions.

        Returns:
            (bool): True if the Condition evaluated to True, False otherwise
        """
        data = data_in

        for transform in self.transforms:
            data = transform.execute(action_execution_strategy, data, accumulator)
        try:
            arguments = self.__update_arguments_with_data(data)
            args = validate_condition_parameters(self._api, arguments, self.action_name, accumulator=accumulator)
        except InvalidArgument as e:
            logger.error('Condition {0} has invalid input {1} which was converted to {2}. Error: {3}. '
                         'Returning False'.format(self.action_name, data_in, data, format_exception_message(e)))
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.ConditionError)
            return False

        try:
            logger.debug('Arguments passed to condition {} are valid'.format(self.id))
            ret = action_execution_strategy.execute(self, accumulator, args)
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.ConditionSuccess)
            if self.is_negated:
                return not ret
            else:
                return ret

        except ExecutionError:
            logger.exception(
                'Error encountered executing condition {0} with arguments {1} and value {2}: Returning False'.format(
                    self.action_name, arguments, data))
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.ConditionError)
            return False

    def __update_arguments_with_data(self, data):
        arguments = []
        for argument in self.arguments:
            if argument.name != self._data_param_name:
                arguments.append(argument)
        arguments.append(Argument(self._data_param_name, value=data))
        return arguments


@event.listens_for(Condition, 'before_update')
def validate_before_update(mapper, connection, target):
    target.validate()
