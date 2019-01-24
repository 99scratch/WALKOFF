import quart.flask_patch

from copy import deepcopy

from quart import jsonify, request
from quart_openapi import Pint, Resource, PintBlueprint
from flask_jwt_extended import jwt_required

from http import HTTPStatus

import walkoff.config
from walkoff import helpers
from walkoff.appgateway import is_app_action_bound
from walkoff.security import permissions_accepted_for_resources, ResourcePermissions
from walkoff.server.problem import Problem
from walkoff.server.returncodes import *
from walkoff.helpers import load_yaml


appapi_bp = PintBlueprint(__name__, 'appapi',
                          base_model_schema=load_yaml(walkoff.config.Config.API_PATH, "appapi.yaml"))


@appapi_bp.route('/apps')
class ReadAllApps(Resource):
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('app_apis', ['read']))
    @appapi_bp.response(HTTPStatus.OK, "Success", appapi_bp.create_ref_validator("ReadAllApps", "responses"))
    async def get(self):
        apps = helpers.list_apps(walkoff.config.Config.APPS_PATH)
        return sorted(apps, key=(lambda app_name: app_name.lower())), SUCCESS


def extract_schema(api, unformatted_fields=('name', 'example', 'placeholder', 'description', 'required')):
    ret = {}
    schema = {}
    for key, value in api.items():
        if key not in unformatted_fields:
            schema[key] = value
        else:
            ret[key] = value
    ret['schema'] = schema
    if 'schema' in ret['schema']:  # flatten the nested schema, happens when parameter is an object
        ret['schema'].update({key: value for key, value in ret['schema'].pop('schema').items()})
    return ret


def format_returns(api, with_event=False):
    ret_returns = []
    for return_name, return_schema in api.items():
        return_schema.update({'status': return_name})
        ret_returns.append(return_schema)
    ret_returns.extend(
        [{'status': 'UnhandledException', 'failure': True, 'description': 'Exception occurred in action'},
         {'status': 'InvalidInput', 'failure': True, 'description': 'Input into the action was invalid'}])
    if with_event:
        ret_returns.append(
            {'status': 'EventTimedOut', 'failure': True, 'description': 'Action timed out out waiting for event'})
    return ret_returns


def format_app_action_api(api, app_name, action_type):
    ret = deepcopy(api)
    if 'returns' in api:
        ret['returns'] = format_returns(ret['returns'], 'event' in api)
    if action_type in ('conditions', 'transforms') or not is_app_action_bound(app_name, api['run']):
        ret['global'] = True
    if 'parameters' in api:
        ret['parameters'] = [extract_schema(param_api) for param_api in ret['parameters']]
    else:
        ret['parameters'] = []
    return ret


def format_all_app_actions_api(api, app_name, action_type):
    actions = []
    for action_name, action_api in api.items():
        ret_action_api = {'name': action_name}
        ret_action_api.update(format_app_action_api(action_api, app_name, action_type))
        actions.append(ret_action_api)
    return sorted(actions, key=lambda action: action['name'])


def format_device_api_full(api, device_name):
    device_api = {'name': device_name}
    unformatted_fields = ('name', 'description', 'encrypted', 'placeholder', 'required')
    if 'description' in api:
        device_api['description'] = api['description']
    device_api['fields'] = [extract_schema(device_field,
                                           unformatted_fields=unformatted_fields)
                            for device_field in api['fields']]

    return device_api


def format_full_app_api(api, app_name):
    ret = {'name': app_name}
    for unformatted_field in ('info', 'tags', 'external_docs'):
        if unformatted_field in api:
            ret[unformatted_field] = api[unformatted_field]
        else:
            ret[unformatted_field] = [] if unformatted_field in ('tags', 'external_docs') else {}
    for formatted_action_field in ('actions', 'conditions', 'transforms'):
        if formatted_action_field in api:
            ret[formatted_action_field[:-1] + '_apis'] = format_all_app_actions_api(api[formatted_action_field],
                                                                                    app_name, formatted_action_field)
        else:
            ret[formatted_action_field[:-1] + '_apis'] = []
    if 'devices' in api:
        ret['device_apis'] = [format_device_api_full(device_api, device_name)
                              for device_name, device_api in api['devices'].items()]
    else:
        ret['device_apis'] = []
    return ret


@appapi_bp.route('/apps/apis')
class ReadAllAppApis(Resource):
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('app_apis', ['read']))
    @appapi_bp.param('field_name', ref=appapi_bp.create_ref_validator('field_name', 'parameters'))
    @appapi_bp.response(HTTPStatus.OK, "Success", appapi_bp.create_ref_validator("ReadAllAppApis", "responses"))
    def get(self):
        field_name = await request.args.get('field_name')
        # TODO: Evaluate whether this is in line with best practices
        if field_name and field_name not in ['info', 'action_apis', 'condition_apis', 'transform_apis', 'device_apis',
                                             'tags', 'external_docs']:
            return Problem(BAD_REQUEST, 'Could not read app api.', '{} is not a valid field name.'.format(field_name))

        ret = []
        for app_name, app_api in walkoff.config.app_apis.items():
            ret.append(format_full_app_api(app_api, app_name))
        if field_name is not None:
            default = [] if field_name not in ('info', 'external_docs') else {}
            ret = [{'name': api['name'], field_name: api.get(field_name, default)} for api in ret]
        return ret, SUCCESS


def app_api_dne_problem(app_name):
    return Problem(OBJECT_DNE_ERROR, 'Could not read app api.', 'App {} does not exist.'.format(app_name))


# @appapi_bp.route('/apps/apis/<app_name>')
# class ReadAppApi(Resource):
#     @jwt_required
#     @permissions_accepted_for_resources(ResourcePermissions('app_apis', ['read']))
#     @appapi_bp.param('app_name', ref=appapi_bp.create_ref_validator('app_name', 'parameters'))
#     @appapi_bp.response(HTTPStatus.OK, "Success", appapi_bp.create_ref_validator("AppApi", "schemas"))
#     def get(self, app_name):
#         api = walkoff.config.app_apis.get(app_name, None)
#         if api is not None:
#             return format_full_app_api(api, app_name), SUCCESS
#         else:
#             return app_api_dne_problem(app_name)


@appapi_bp.route('/apps/apis/<app_name>')
class ReadAppApiField(Resource):
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('app_apis', ['read']))
    @appapi_bp.param('app_name', ref=appapi_bp.create_ref_validator('app_name', 'parameters'))
    @appapi_bp.param('field_name', ref=appapi_bp.create_ref_validator('field_name', 'parameters'))
    @appapi_bp.response(HTTPStatus.OK, "Success", appapi_bp.create_ref_validator("AppApi", "schemas"))
    def get(self, app_name):
        field_name = await request.args.get('field_name')
        # TODO: Evaluate whether this is in line with best practices
        if field_name not in ['info', 'action_apis', 'condition_apis', 'transform_apis', 'device_apis', 'tags',
                              'externalDocs']:
            return Problem(BAD_REQUEST, 'Could not read app api.', '{} is not a valid field name.'.format(field_name))

        api = walkoff.config.app_apis.get(app_name, None)
        if api is not None:
            r = format_full_app_api(api, app_name)
            if field_name is not None:
                return r[field_name], SUCCESS
            else:
                return r, SUCCESS
        else:
            return app_api_dne_problem(app_name)
