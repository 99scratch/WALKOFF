from __future__ import with_statement

import os
import sys

sys.path.append(os.getcwd())

# Need all these imports
from walkoff.serverdb.message import *
from walkoff.serverdb.mixins import *
from walkoff.serverdb.resource import *
from walkoff.serverdb.scheduledtasks import *
from walkoff.serverdb.tokens import *
from walkoff.serverdb.user import *
from walkoff.migrations.database.commonenv import run
from walkoff.extensions import db
from walkoff.server.app import create_app
import walkoff.config


# unclear if commented out app creation is necessary, but may need it in the future
# walkoff.config.initialize()
# app = create_app()
# app_context = app.test_request_context()
# app_context.push()

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = db.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


run(target_metadata)
