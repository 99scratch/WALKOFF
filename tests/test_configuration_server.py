import json
import os
import shutil

from quart import current_app

import walkoff.config
from tests.util.servertestcase import ServerTestCase
from walkoff.server.returncodes import *


class TestConfigurationServer(ServerTestCase):
    def setUp(self):
        config_fields = [x for x in dir(walkoff.config.Config) if
                         not x.startswith('__') and type(getattr(walkoff.config.Config, x)).__name__
                         in ['str', 'unicode', 'dict']]
        self.original_configs = {key: getattr(walkoff.config.Config, key) for key in config_fields}
        try:
            with open(walkoff.config.Config.CONFIG_PATH) as config_file:
                self.original_config_file = config_file.read()
        except:
            self.original_config_file = '{}'

    def tearDown(self):
        for key, value in self.original_configs.items():
            setattr(walkoff.config.Config, key, value)
        with open(walkoff.config.Config.CONFIG_PATH, 'w') as config_file:
            config_file.write(self.original_config_file)
        if os.path.exists(os.path.join('.', 'abc')):
            shutil.rmtree(os.path.join('.', 'abc'))

    def test_get_configuration(self):
        expected = {'db_path': walkoff.config.Config.DB_PATH,
                    'logging_config_path': walkoff.config.Config.LOGGING_CONFIG_PATH,
                    'host': walkoff.config.Config.HOST,
                    'port': int(walkoff.config.Config.PORT),
                    'walkoff_db_type': walkoff.config.Config.WALKOFF_DB_TYPE,
                    'number_threads_per_process': int(walkoff.config.Config.NUMBER_THREADS_PER_PROCESS),
                    'number_processes': int(walkoff.config.Config.NUMBER_PROCESSES),
                    'access_token_duration': int(current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].seconds / 60),
                    'refresh_token_duration': int(current_app.config['JWT_REFRESH_TOKEN_EXPIRES'].days),
                    'zmq_results_address': walkoff.config.Config.ZMQ_RESULTS_ADDRESS,
                    'zmq_communication_address': walkoff.config.Config.ZMQ_COMMUNICATION_ADDRESS,
                    'cache': walkoff.config.Config.CACHE}
        response = self.get_with_status_check('/api/configuration', headers=self.headers)
        self.assertDictEqual(response, expected)

    def put_post_to_config(self, verb):
        send_func = self.put_with_status_check if verb == 'put' else self.patch_with_status_check
        data = {"db_path": "db_path_reset",
                "logging_config_path": "logging_config_reset",
                "host": "host_reset",
                "port": 1100,
                "walkoff_db_type": "postgresql",
                "number_threads_per_process": 5,
                "number_processes": 10,
                "access_token_duration": 20,
                "refresh_token_duration": 35}

        response = send_func('/api/configuration', headers=self.headers, data=json.dumps(data),
                             content_type='application/json')

        expected = {walkoff.config.Config.DB_PATH: "db_path_reset",
                    walkoff.config.Config.LOGGING_CONFIG_PATH: "logging_config_reset",
                    walkoff.config.Config.HOST: "host_reset",
                    walkoff.config.Config.PORT: 1100,
                    walkoff.config.Config.WALKOFF_DB_TYPE: "postgresql",
                    walkoff.config.Config.NUMBER_THREADS_PER_PROCESS: 5,
                    walkoff.config.Config.NUMBER_PROCESSES: 10}

        for actual, expected_ in expected.items():
            self.assertEqual(actual, expected_)

        self.assertEqual(current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].seconds, 20 * 60)
        self.assertEqual(current_app.config['JWT_REFRESH_TOKEN_EXPIRES'].days, 35)

    def test_set_configuration_put(self):
        self.put_post_to_config('put')

    def test_set_configuration_patch(self):
        self.put_post_to_config('patch')

    def test_set_configuration_invalid_token_durations(self):
        access_token_duration = current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].seconds
        refresh_token_duration = current_app.config['JWT_REFRESH_TOKEN_EXPIRES'].days
        data = {"access_token_duration": 60 * 25,
                "refresh_token_duration": 1}
        self.put_with_status_check('/api/configuration', headers=self.headers, data=json.dumps(data),
                                   content_type='application/json', status_code=BAD_REQUEST)

        self.assertEqual(current_app.config['JWT_ACCESS_TOKEN_EXPIRES'].seconds, access_token_duration)
        self.assertEqual(current_app.config['JWT_REFRESH_TOKEN_EXPIRES'].days, refresh_token_duration)
