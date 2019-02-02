import json
from redis import Redis
import walkoff.config


config = walkoff.config.Config()
config.load_env_vars()

redis_cache = Redis(host=config.CACHE["host"], port=config.CACHE["port"])


def get_app_action_api(app, action):
    """
    Gets the api for a given app and action

    Args:
        app (str): Name of the app
        action (str): Name of the action

    Returns:
        (tuple(str, dict)) The name of the function to execute and its parameters
    """
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            action_api = app_api['actions'][action]
            run = action_api['run']
            return run, action_api.get('parameters', [])
        except KeyError:
            raise UnknownAppAction(app, action)


def get_app_action_default_return(app, action):
    """
    Gets the default return code for a given app and action

    Args:
        app (str): Name of the app
        action (str): Name of the action

    Returns:
        (str): The name of the default return code or Success if none defined
    """
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            action_api = app_api['actions'][action]
            if 'default_return' in action_api:
                return action_api['default_return']
            else:
                return 'Success'
        except KeyError:
            raise UnknownAppAction(app, action)


def get_app_action_return_is_failure(app, action, status):
    """
    Checks the api for whether a status code is a failure code for a given app and action

    Args:
        app (str): Name of the app
        action (str): Name of the action
        status (str): Name of the status

    Returns:
        (boolean): True if status is a failure code, false otherwise
    """
    if status == 'UnhandledException':
        return True
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            action_api = app_api['actions'][action]
            if 'failure' in action_api['returns'][status]:
                return action_api['returns'][status]['failure']
            else:
                return False
        except KeyError:
            raise UnknownAppAction(app, action)


def get_app_device_api(app, device_type):
    """Gets the device API

    Args:
        app (str): App name
        device_type (str): The name of the device type

    Returns:
        dict: The device API

    Raises:
        UnknownApp: If an app name is passed in that is not in the App API
        UnknownDevice: If a device type is passed in that does not correspond to the App
    """
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            return app_api['devices'][device_type]
        except KeyError:
            raise UnknownDevice(app, device_type)


def split_api_params(api, data_param_name):
    """Return a dict with data_param_name entry not included

    Args:
        api (dict): The API
        data_param_name (str): The name of the param to exclude from the dictionary

    Returns:
        dict: The new dictionary with the data_param_name entry removed
    """
    args = []
    for api_param in api:
        if api_param['name'] != data_param_name:
            args.append(api_param)
    return args


def get_condition_api(app, condition):
    """Gets the condition API

    Args:
        app (str): The name of the App
        condition (str): The name of the condition

    Returns:
        dict: The condition API

    Raises:
        UnknownApp: If no App with that name exists
        UnknownCondition: If no Condition exists for that App
    """
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            condition_api = app_api['conditions'][condition]
            run = condition_api['run']
            return condition_api['data_in'], run, condition_api.get('parameters', [])
        except KeyError:
            raise UnknownCondition(app, condition)


def get_transform_api(app, transform):
    """Gets the transform API

        Args:
            app (str): The name of the App
            transform (str): The name of the Transform

        Returns:
            dict: The transform API

        Raises:
            UnknownApp: If no App with that name exists
            UnknownTransform: If no Transform exists for that App
        """
    try:
        app_api = json.loads(redis_cache.hget("app-apis", app))
        if not app_api:
            raise KeyError
    except KeyError:
        raise UnknownApp(app)
    else:
        try:
            transform_api = app_api['transforms'][transform]
            run = transform_api['run']
            return transform_api['data_in'], run, transform_api.get('parameters', [])
        except KeyError:
            raise UnknownTransform(app, transform)


# Exceptions
class InvalidAppStructure(Exception):
    pass


class UnknownApp(Exception):
    def __init__(self, app):
        super(UnknownApp, self).__init__('Unknown app {0}'.format(app))
        self.app = app


class UnknownFunction(Exception):
    def __init__(self, app, function_name, function_type):
        self.message = 'Unknown {0} {1} for app {2}'.format(function_type, function_name, app)
        super(UnknownFunction, self).__init__(self.message)
        self.app = app
        self.function = function_name


class UnknownAppAction(UnknownFunction):
    def __init__(self, app, action_name):
        super(UnknownAppAction, self).__init__(app, action_name, 'action')


class UnknownDevice(Exception):
    def __init__(self, app, device_type):
        super(UnknownDevice, self).__init__('Unknown device {0} for device {1} '.format(app, device_type))
        self.app = app
        self.device_type = device_type


class InvalidArgument(Exception):
    def __init__(self, message, errors=None):
        self.message = message
        self.errors = errors or {}
        super(InvalidArgument, self).__init__(self.message)


class UnknownCondition(UnknownFunction):
    def __init__(self, app, condition_name):
        super(UnknownCondition, self).__init__(app, condition_name, 'condition')


class UnknownTransform(UnknownFunction):
    def __init__(self, app, transform_name):
        super(UnknownTransform, self).__init__(app, transform_name, 'transform')


class InvalidApi(Exception):
    pass
