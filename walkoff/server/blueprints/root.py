import logging
import os

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from quart import current_app
from quart import render_template, send_from_directory, Blueprint
from sqlalchemy.exc import SQLAlchemyError

import walkoff.config
from walkoff import helpers
from walkoff.executiondb.device import App
from walkoff.extensions import db
from walkoff.server.problem import Problem
from walkoff.server.returncodes import SERVER_ERROR

logger = logging.getLogger(__name__)

root_page = Blueprint('root_page', __name__)


# Custom static data
@root_page.route('client/<path:filename>')
def client_app_folder(filename):
    return send_from_directory(os.path.abspath(walkoff.config.Config.CLIENT_PATH), filename)


@root_page.route('/')
@root_page.route('playbook')
@root_page.route('execution')
@root_page.route('scheduler')
@root_page.route('devices')
@root_page.route('messages')
@root_page.route('metrics')
@root_page.route('settings')
def default():
    return send_from_directory(os.path.abspath(walkoff.config.Config.CLIENT_PATH), "dist/index.html")
    # return render_template("index.html")


@root_page.route('interfaces/<interface_name>')
def app_page(interface_name):
    return render_template("index.html")


@root_page.route('login')
def login_page():
    return render_template("login.html")


@root_page.errorhandler(SQLAlchemyError)
def handle_database_errors(e):
    current_app.logger.exception('Caught an unhandled SqlAlchemy exception.')
    return Problem(SERVER_ERROR, 'A database error occurred.', e.__class__.__name__)


@root_page.errorhandler(500)
def handle_generic_server_error(e):
    current_app.logger.exception('Caught an unhandled error.')
    return Problem(SERVER_ERROR, 'An error occurred in the server.', e.__class__.__name__)


@root_page.before_app_first_request
def create_user():
    from walkoff.serverdb import add_user, User, Role, initialize_default_resources_admin, \
        initialize_default_resources_guest
    from sqlalchemy_utils import database_exists, create_database

    if not database_exists(db.engine.url):
        create_database(db.engine.url)
    db.create_all()

    alembic_cfg = Config(walkoff.config.Config.ALEMBIC_CONFIG, ini_section="walkoff",
                         attributes={'configure_logger': False})

    # This is necessary for a flask database
    connection = db.engine.connect()
    context = MigrationContext.configure(connection)
    script = ScriptDirectory.from_config(alembic_cfg)
    context.stamp(script, "head")

    # Setup admin and guest roles
    initialize_default_resources_admin()
    initialize_default_resources_guest()

    # Setup admin user
    admin_role = Role.query.filter_by(id=1).first()
    admin_user = User.query.filter_by(username="admin").first()
    if not admin_user:
        add_user(username='admin', password='admin', roles=[1])
    elif admin_role not in admin_user.roles:
        admin_user.roles.append(admin_role)

    db.session.commit()

    apps = set(helpers.list_apps(walkoff.config.Config.APPS_PATH)) - set([_app.name
                                                                          for _app in
                                                                          current_app.running_context.execution_db.session.query(
                                                                              App).all()])
    current_app.logger.debug('Found new apps: {0}'.format(apps))
    for app_name in apps:
        current_app.running_context.execution_db.session.add(App(name=app_name, devices=[]))
    db.session.commit()
    current_app.running_context.execution_db.session.commit()
    reschedule_all_workflows()
    current_app.logger.handlers = logging.getLogger('server').handlers


def reschedule_all_workflows():
    from walkoff.serverdb.scheduledtasks import ScheduledTask
    current_app.logger.info('Scheduling workflows')
    for task in (task for task in ScheduledTask.query.all() if task.status == 'running'):
        current_app.logger.debug('Rescheduling task {} (id={})'.format(task.name, task.id))
        task._start_workflows()
