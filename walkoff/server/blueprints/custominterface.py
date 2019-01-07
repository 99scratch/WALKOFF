import quart.flask_patch

import os

from quart import Blueprint, render_template, g, abort
from flask_jwt_extended import jwt_required
from jinja2 import TemplateNotFound

custom_interface_page = Blueprint('custom_interface', 'interfaces', template_folder=os.path.abspath('interfaces'),
                                  static_folder='static')


@custom_interface_page.url_value_preprocessor
def static_request_handler(endpoint, values):
    g.interface = values.pop('interface', None)
    static_path = os.path.abspath(os.path.join('interfaces', g.interface, 'interface', 'static'))
    custom_interface_page.static_folder = static_path


@custom_interface_page.route('/', methods=['GET'], defaults={'page': 'index'})
@custom_interface_page.route('/<page>')
@jwt_required
def read_app(page):
    path = '{0}/interface/templates/{1}.html'.format(g.interface, page)
    try:
        return render_template(path)
    except TemplateNotFound:
        abort(404)
