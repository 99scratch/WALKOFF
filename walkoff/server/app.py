import logging
import os

import connexion
from flask import Blueprint
from flask import Flask
from flask_swagger_ui import get_swaggerui_blueprint
from healthcheck import HealthCheck
from jinja2 import FileSystemLoader
from yaml import Loader, load

import interfaces
import walkoff.config
from walkoff.extensions import db, jwt
from walkoff.helpers import import_submodules
from walkoff.server import context
from walkoff.server.blueprints import custominterface, workflowresults, notifications, console, root

logger = logging.getLogger(__name__)


def register_blueprints(flaskapp, separate_interfaces=False):
    flaskapp.logger.info('Registering builtin blueprints')
    flaskapp.register_blueprint(custominterface.custom_interface_page, url_prefix='/custominterfaces/<interface>')
    flaskapp.register_blueprint(workflowresults.workflowresults_page, url_prefix='/walkoffapi/streams/workflowqueue')
    flaskapp.register_blueprint(notifications.notifications_page, url_prefix='/walkoffapi/streams/messages')
    flaskapp.register_blueprint(console.console_page, url_prefix='/walkoffapi/streams/console')
    flaskapp.register_blueprint(root.root_page, url_prefix='/')
    for blueprint in (workflowresults.workflowresults_page, notifications.notifications_page, console.console_page):
        blueprint.cache = flaskapp.running_context.cache
    if not separate_interfaces:
        __register_all_app_blueprints(flaskapp, main_app=True)


def __get_blueprints_in_module(module):
    blueprints = [getattr(module, field)
                  for field in dir(module) if (not field.startswith('__')
                                               and isinstance(getattr(module, field), Blueprint))]
    return blueprints


def __register_blueprint(flaskapp, blueprint, url_prefix):
    if isinstance(blueprint, interfaces.AppBlueprint):
        blueprint.cache = flaskapp.running_context.cache
    url_prefix = '{0}{1}'.format(url_prefix, blueprint.url_suffix) if blueprint.url_suffix else url_prefix
    blueprint.url_prefix = url_prefix
    flaskapp.register_blueprint(blueprint, url_prefix=url_prefix)
    flaskapp.logger.info('Registered custom interface blueprint at url prefix {}'.format(url_prefix))


def __register_app_blueprints(flaskapp, app_name, blueprints):
    url_prefix = '/interfaces/{0}'.format(app_name.split('.')[-1])
    for blueprint in blueprints:
        __register_blueprint(flaskapp, blueprint, url_prefix)


def __register_all_app_blueprints(flaskapp, main_app=False):
    if not main_app:
        flaskapp.logger.info('Registering builtin blueprints')
        flaskapp.register_blueprint(custominterface.custom_interface_page, url_prefix='/custominterfaces/<interface>')
        flaskapp.register_blueprint(root.root_page, url_prefix='/')

    imported_apps = import_submodules(interfaces)
    for interface_name, interfaces_module in imported_apps.items():
        try:
            interface_blueprints = []
            for submodule in import_submodules(interfaces_module, recursive=True).values():
                interface_blueprints.extend(__get_blueprints_in_module(submodule))
        except ImportError:
            pass
        else:
            __register_app_blueprints(flaskapp, interface_name, interface_blueprints)


def register_swagger_blueprint(flaskapp):
    # register swagger API docs location
    swagger_path = os.path.join(walkoff.config.Config.API_PATH, 'composed_api.yaml')
    swagger_yaml = load(open(swagger_path), Loader=Loader)
    swaggerui_blueprint = get_swaggerui_blueprint(walkoff.config.Config.SWAGGER_URL, swagger_yaml,
                                                  config={'spec': swagger_yaml})
    flaskapp.register_blueprint(swaggerui_blueprint, url_prefix=walkoff.config.Config.SWAGGER_URL)
    flaskapp.logger.info("Registered blueprint for swagger API docs at url prefix /walkoffapi//docs")


def add_health_check(_app):
    health = HealthCheck(_app, '/health')
    from walkoff.server.endpoints.health import checks
    for check in checks:
        health.add_check(check)


def create_app(interface_app=False):
    if not interface_app:
        connexion_app = _app = connexion.App(__name__, specification_dir='../api/', options={'swagger_ui': False})
        _app = connexion_app.app
    else:
        _app = Flask(__name__)

    _app.jinja_loader = FileSystemLoader(['walkoff/templates'])
    _app.config.from_object(walkoff.config.Config)

    try:
        db.init_app(_app)
    except Exception as e:
        logger.error("Error initializing walkoff database. Please make sure all settings are properly configured in the"
                     "config file, and that all necessary environment variables are set correctly."
                     "Error message: {}".format(str(e)))
        os._exit(1)

    if not interface_app:
        jwt.init_app(_app)
        connexion_app.add_api('composed_api.yaml')
        _app.running_context = context.Context()
        register_blueprints(_app, walkoff.config.Config.SEPARATE_INTERFACES)
        register_swagger_blueprint(_app)
    else:
        _app.running_context = context.Context(executor=False)
        __register_all_app_blueprints(_app)

    add_health_check(_app)

    return _app
