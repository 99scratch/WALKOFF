import logging
from copy import deepcopy

from sqlalchemy import Column, ForeignKey, String, orm, event
from sqlalchemy.orm import relationship
from sqlalchemy_utils import UUIDType

from walkoff.appgateway import get_transform
from walkoff.appgateway.apiutil import split_api_params, get_transform_api, UnknownApp, InvalidArgument, \
    UnknownTransform
from walkoff.appgateway.validator import validate_transform_parameters
from walkoff.events import WalkoffEvent
from walkoff.executiondb import Execution_Base
from walkoff.executiondb.argument import Argument
from walkoff.executiondb.executionelement import ExecutionElement
from walkoff.helpers import ExecutionError

logger = logging.getLogger(__name__)


class Transform(ExecutionElement, Execution_Base):
    __tablename__ = 'transform'
    condition_id = Column(UUIDType(binary=False), ForeignKey('condition.id', ondelete='CASCADE'))
    app_name = Column(String(80), nullable=False)
    action_name = Column(String(80), nullable=False)
    arguments = relationship('Argument', cascade='all, delete, delete-orphan', passive_deletes=True)
    children = ('arguments',)

    def __init__(self, app_name, action_name, id=None, arguments=None, errors=None):
        """Initializes a new Transform object. A Transform is used to transform input into a workflow.

        Args:
            app_name (str): The app name associated with this transform
            action_name (str): The action name for the transform.
            id (str|UUID, optional): Optional UUID to pass into the Transform. Must be UUID object or valid UUID string.
                Defaults to None.
            arguments (list[Argument], optional): Dictionary of Argument keys to Argument values.
                This dictionary will be converted to a dictionary of str:Argument. Defaults to None.
        """
        ExecutionElement.__init__(self, id, errors)
        self.app_name = app_name
        self.action_name = action_name

        self._data_param_name = None
        self._api = None

        self.arguments = []
        if arguments:
            self.arguments = arguments
        self.validate()

    def validate(self):
        """Validates the object"""
        errors = []
        try:
            self._data_param_name, run, self._api = get_transform_api(self.app_name, self.action_name)
            get_transform(self.app_name, run)
            tmp_api = split_api_params(self._api, self._data_param_name)
            validate_transform_parameters(tmp_api, self.arguments, self.action_name)
        except UnknownApp:
            errors.append('Unknown app {}'.format(self.app_name))
        except UnknownTransform:
            errors.append('Unknown transform {}'.format(self.action_name))
        except InvalidArgument as e:
            errors.extend(e.errors)
        self.errors = errors

    @orm.reconstructor
    def init_on_load(self):
        """Loads all necessary fields upon Condition being loaded from database"""
        if not self.errors:
            errors = []
            try:
                self._data_param_name, run, self._api = get_transform_api(self.app_name, self.action_name)
                get_transform(self.app_name, run)
            except UnknownApp:
                errors.append('Unknown app {}'.format(self.app_name))
            except UnknownTransform:
                errors.append('Unknown transform {}'.format(self.action_name))
            self.errors = errors

    def execute(self, action_execution_strategy, data_in, accumulator):
        """Executes the transform.

        Args:
            action_execution_strategy: The strategy used to execute the action (e.g. LocalActionExecutionStrategy)
            data_in: The input to the condition, the last executed action of the workflow or the input to a trigger.
            accumulator (dict): A record of executed actions and their results. Of form {action_name: result}.

        Returns:
            (obj): The transformed data
        """
        original_data_in = deepcopy(data_in)
        try:
            arguments = self.__update_arguments_with_data(data_in)
            args = validate_transform_parameters(self._api, arguments, self.action_name, accumulator=accumulator)
        except InvalidArgument as e:
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.TransformError)
            logger.error('Transform {0} has invalid input {1}. Error: {2}. '
                         'Returning unmodified data'.format(self.action_name, original_data_in, str(e)))
            return original_data_in

        try:
            result = action_execution_strategy.execute(self, accumulator, args)
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.TransformSuccess)
            return result

        except ExecutionError:
            logger.exception(
                'Transform {0} (id={1}) encountered an error. Returning unmodified data'.format(
                    self.action_name, str(self.id)))
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.TransformError)

        return original_data_in

    def __update_arguments_with_data(self, data):
        arguments = []
        for argument in self.arguments:
            if argument.name != self._data_param_name:
                arguments.append(argument)
        arguments.append(Argument(self._data_param_name, value=data))
        return arguments


@event.listens_for(Transform, 'before_update')
def validate_before_update(mapper, connection, target):
    target.validate()
