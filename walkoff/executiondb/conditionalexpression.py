import logging
from uuid import uuid4

from sqlalchemy import Column, ForeignKey, Enum, orm, Boolean, event
from sqlalchemy.orm import relationship, backref
from sqlalchemy_utils import UUIDType

from walkoff.events import WalkoffEvent
from walkoff.executiondb import Execution_Base
from walkoff.executiondb.executionelement import ExecutionElement

logger = logging.getLogger(__name__)

valid_operators = ('and', 'or', 'xor')


class ConditionalExpression(ExecutionElement, Execution_Base):
    __tablename__ = 'conditional_expression'
    id = Column(UUIDType(binary=False), primary_key=True, default=uuid4)
    action_id = Column(UUIDType(binary=False), ForeignKey('action.id', ondelete='CASCADE'))
    branch_id = Column(UUIDType(binary=False), ForeignKey('branch.id', ondelete='CASCADE'))
    parent_id = Column(UUIDType(binary=False), ForeignKey(id, ondelete='CASCADE'))
    operator = Column(Enum(*valid_operators, name='operator_types'), nullable=False)
    is_negated = Column(Boolean, default=False)
    child_expressions = relationship('ConditionalExpression',
                                     cascade='all, delete-orphan',
                                     backref=backref('parent', remote_side=id), passive_deletes=True)
    conditions = relationship('Condition', cascade='all, delete-orphan', passive_deletes=True)
    children = ('child_expressions', 'conditions')

    def __init__(self, operator='and', id=None, is_negated=False, child_expressions=None, conditions=None, errors=None):
        """Initializes a new ConditionalExpression object

        Args:
            operator (and|or|xor, optional): The operator to be used between the conditions. Defaults to 'and'.
            id (str|UUID, optional): Optional UUID to pass into the Action. Must be UUID object or valid UUID string.
                Defaults to None.
            is_negated(bool, optional): Whether or not the expression should be negated. Defaults to False.
            child_expressions (list[ConditionalExpression], optional): Child ConditionalExpression objects for this
                object. Defaults to None.
            conditions (list[Condition], optional): Condition objects for this object. Defaults to None.
        """
        ExecutionElement.__init__(self, id, errors)
        self.operator = operator
        self.is_negated = is_negated
        if child_expressions:
            self._construct_children(child_expressions)
        self.child_expressions = child_expressions if child_expressions is not None else []
        self.conditions = conditions if conditions is not None else []
        self.__operator_lookup = {'and': self._and,
                                  'or': self._or,
                                  'xor': self._xor}

        self.validate()

    @orm.reconstructor
    def init_on_load(self):
        """Loads all necessary fields upon ConditionalExpression being loaded from database"""
        self.__operator_lookup = {'and': self._and,
                                  'or': self._or,
                                  'xor': self._xor}

    def validate(self):
        pass

    def _construct_children(self, child_expressions):
        for child in child_expressions:
            child.parent = self

    def execute(self, action_execution_strategy, data_in, accumulator):
        """Executes the ConditionalExpression object, determining if the statement evaluates to True or False.

        Args:
            action_execution_strategy: The strategy used to execute the action (e.g. LocalActionExecutionStrategy)
            data_in (dict): The input to the Transform objects associated with this ConditionalExpression.
            accumulator (dict): The accumulated data from previous Actions.

        Returns:
            (bool): True if the Condition evaluated to True, False otherwise
        """
        result = self.__operator_lookup[self.operator](action_execution_strategy, data_in, accumulator)
        if self.is_negated:
            result = not result
        if result:
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.ConditionalExpressionTrue)
        else:
            WalkoffEvent.CommonWorkflowSignal.send(self, event=WalkoffEvent.ConditionalExpressionFalse)
        return result

    def _and(self, action_execution_strategy, data_in, accumulator):
        return (all(condition.execute(action_execution_strategy, data_in, accumulator)
                    for condition in self.conditions)
                and all(expression.execute(action_execution_strategy, data_in, accumulator)
                        for expression in self.child_expressions))

    def _or(self, action_execution_strategy, data_in, accumulator):
        if not self.conditions and not self.child_expressions:
            return True
        return (any(condition.execute(action_execution_strategy, data_in, accumulator)
                    for condition in self.conditions)
                or any(expression.execute(action_execution_strategy, data_in, accumulator)
                       for expression in self.child_expressions))

    def _xor(self, action_execution_strategy, data_in, accumulator):
        if not self.conditions and not self.child_expressions:
            return True
        is_one_found = False
        for executable_group in (self.conditions, self.child_expressions):
            for executable in executable_group:
                if executable.execute(action_execution_strategy, data_in, accumulator):
                    if is_one_found:
                        return False
                    is_one_found = True
        return is_one_found


@event.listens_for(ConditionalExpression, 'before_update')
def validate_before_update(mapper, connection, target):
    target.validate()
