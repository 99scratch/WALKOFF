import os
import stat
import subprocess
import sys
import tarfile
from base64 import b64encode
from distutils.spawn import find_executable

import click
import requests
import yaml
from kubernetes import client as k8s_client
from kubernetes import config

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import datetime
import uuid


@click.command()
@click.pass_context
@click.option('-a', '--archive', help='Archived installation for offline installation')
# @click.option('-v', '--values', help='Path to a Helm chart values YAML to use in the installation')
def install(ctx, archive):
    """
    Installs WALKOFF to a Kubernetes cluster. Requires Helm and Kubectl to be installed and pointed at a cluster.

    If an archive is provided, installation will take place without requiring an internet connection.
    """
    if archive:
        offline_install(ctx)
    else:
        online_install(ctx)


def offline_install(ctx):
    # TODO: Offline install must execute separate script to install walkoffctl
    if not find_executable('helm'):
        install_helm_offline(ctx)
    setup_helm()
    add_charts_offline()
    install_docker_repository()
    populate_docker_repository()
    # if not values:
    # values = get_chart_configuration()
    # install_walkoff(values)


def helm_command(args, tiller, exit_on_err=True):
    cmd = ['helm'] + args + ['--tiller-namespace', tiller]
    try:
        r = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        click.echo(r.decode('utf-8'))
    except subprocess.CalledProcessError as e:
        if exit_on_err:
            click.echo('Helm returned error code {}: {}'.format(e.returncode, e.output.decode('utf-8')))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            sys.exit(1)
        else:
            click.echo('{}'.format(e.output.decode('utf-8')))


def kubectl_command(args, namespace, exit_on_err=True):
    cmd = ['kubectl'] + args
    if namespace is not None:
        cmd += ['--namespace', namespace]
    try:
        r = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        click.echo(r.decode('utf-8'))
    except subprocess.CalledProcessError as e:
        if exit_on_err:
            click.echo('Kubectl returned error code {}: {}'.format(e.returncode, e.output.decode('utf-8')))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            sys.exit(1)
        else:
            click.echo('{}'.format(e.output.decode('utf-8')))


def online_install(ctx):
    click.echo('Installing WALKOFF to Kubernetes cluster with Internet access.')
    try:
        config_dir = os.environ.get('KUBECONFIG', os.path.join(os.path.expanduser("~"), ".kube", "config"))
        config_dir = click.prompt("Enter location of kubernetes config",
                                  default=config_dir)

        contexts, current = config.list_kube_config_contexts(config_file=config_dir)
        contexts = [context["name"] for context in contexts]
        current = current["name"]

        context = click.prompt("Available contexts: {}\nEnter context to install WALKOFF to".format(contexts),
                               default=current)

        config.load_kube_config(config_file=config_dir, context=context)
        k8s_api = k8s_client.CoreV1Api()
        k8s_custom_api = k8s_client.CustomObjectsApi()
    except IOError as e:
        print("Could not open config: {}".format(e))
        return

    namespaces = k8s_api.list_namespace()
    namespaces = [ns.metadata.name for ns in namespaces.items]
    namespace = click.prompt("Available namespaces: {}\nEnter namespace to install WALKOFF in".format(namespaces),
                             default="default")

    if namespace not in namespaces:
        if click.confirm("{} does not exist - do you want to create it now?"):
            new_namespace = k8s_client.V1Namespace(metadata={'name': namespace})
            try:
                k8s_api.create_namespace(new_namespace)
            except k8s_client.rest.ApiException as e:
                click.echo("Error creating namespace:\n{}".format(str(e)))
                click.echo('You should use the uninstall command to rollback changes made by this installer.')
                return

    tiller_namespace = click.prompt('Enter the namespace your Tiller service resides in',
                                    default='kube-system')

    click.echo("Generating ZMQ certificates for WALKOFF.")
    if subprocess.call(['python', 'scripts/generate_certificates.py']) != 0:
        click.echo("Error generating ZMQ certificates.")
        return

    click.echo("Adding ZMQ certificates to Kubernetes secrets.")
    kubectl_command(['create', 'secret', 'generic', 'walkoff-zmq-private-keys',
                     '--from-file=server.key_secret=./.certificates/private_keys/server.key_secret',
                     '--from-file=client.key_secret=./.certificates/private_keys/client.key_secret'],
                    namespace)

    kubectl_command(['create', 'secret', 'generic', 'walkoff-zmq-public-keys',
                     '--from-file=server.key=./.certificates/public_keys/server.key',
                     '--from-file=client.key=./.certificates/public_keys/client.key'],
                    namespace)

    existing_secrets = k8s_api.list_namespaced_secret(namespace)
    redis_secret_name = None
    redis_hostname = None
    if click.confirm('Is there an existing Redis instance WALKOFF should use?'):
        redis_hostname = click.prompt('Enter the Redis hostname (if it is not in the same Kubernetes namespace '
                                      'as WALKOFF, enter a fully qualified domain name)')
        if click.confirm("Is the Redis password already stored in a Kubernetes secret?"):
            redis_secret_name = click.prompt('Available secrets: {}\nEnter the name of the secret the Redis password '
                                             'is stored in with a key of "redis-password" (leave blank for none): ',
                                             default="")
            if redis_secret_name not in existing_secrets:
                redis_secret_name = None
                click.echo('No secret with that name in this namespace. Creating a new secret to store password.')

    if not redis_secret_name:
        redis_secret_name = "walkoff-redis-secret"
        new_pass = click.prompt('Enter a password for the Redis instance', hide_input=True, confirmation_prompt=True,
                                default='walkoff')
        redis_secret_obj = k8s_client.V1Secret(metadata={'name': redis_secret_name},
                                               data={'redis-password': b64encode(new_pass.encode('utf-8'))
                                               .decode('utf-8')})
        try:
            k8s_api.create_namespaced_secret(namespace, redis_secret_obj)
        except k8s_client.rest.ApiException as e:
            click.echo("Error creating secret:\n{}".format(str(e)))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    with open("k8s_manifests/setupfiles/redis-helm-values.yaml", 'r+') as f:
        try:
            y = yaml.load(f)
            y['existingSecret'] = redis_secret_name
            f.seek(0)
            f.truncate()
            yaml.dump(y, f, default_flow_style=False)
        except yaml.YAMLError as e:
            click.echo("Error reading k8s_manifests/setupfiles/redis-helm-values.yaml")
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    if not redis_hostname:
        redis_hostname = 'walkoff-redis'
        helm_command(['install', 'stable/redis',
                      '--name', redis_hostname,
                      '--values', 'k8s_manifests/setupfiles/redis-helm-values.yaml',
                      '--set', 'existingSecret={}'.format(redis_secret_name)],
                     tiller_namespace)

    execution_secret_name = None
    execution_db_hostname = None
    if click.confirm('Do you have an existing PostgreSQL database to store WALKOFF execution data in?'):
        execution_db_hostname = click.prompt('Enter the database hostname (if it is not in the same Kubernetes '
                                             'namespace as WALKOFF, enter a fully qualified domain name)')
        execution_db_username = click.prompt('Enter a username that is able to create/read/write/update databases')
        if click.confirm("Is the PostgreSQL password already stored in a Kubernetes secret?"):
            execution_secret_name = click.prompt('Available secrets: {}\nEnter the name of the secret the PostgreSQL '
                                                 'password is stored in with a key of "postgres-password" '
                                                 '(leave blank for none): ',
                                                 default="")
            if execution_secret_name not in existing_secrets:
                execution_secret_name = None
                click.echo('No secret with that name in this namespace. Creating a new secret to store password.')

    if not execution_secret_name:
        execution_secret_name = "walkoff-postgres-execution-secret"
        execution_db_username = click.prompt('Enter a username to create', default='walkoff')
        execution_db_password = click.prompt('Enter a password for the PostgreSQL instance', hide_input=True,
                                confirmation_prompt=True, default='walkoff')
        execution_secret_obj = k8s_client.V1Secret(metadata={'name': execution_secret_name}, data={
            'postgres-password': b64encode(execution_db_password.encode('utf-8')).decode('utf-8')})
        try:
            k8s_api.create_namespaced_secret(namespace, execution_secret_obj)
        except k8s_client.rest.ApiException as e:
            click.echo("Error creating secret:\n{}".format(str(e)))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    with open("k8s_manifests/setupfiles/execution-postgres-helm-values.yaml", 'r+') as f:
        try:
            y = yaml.load(f)
            y['postgresqlUsername'] = execution_db_username
            y['postgresqlPassword'] = execution_db_password
            f.seek(0)
            f.truncate()
            yaml.dump(y, f, default_flow_style=False)
        except yaml.YAMLError as e:
            click.echo("Error reading k8s_manifests/setupfiles/execution-postgres-helm-values.yaml")
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    if not execution_db_hostname:
        helm_command(['install', 'stable/postgresql',
                      '--name', 'execution-db',
                      '--values', 'k8s_manifests/setupfiles/execution-postgres-helm-values.yaml'],
                     tiller_namespace)
        execution_db_hostname = 'execution-db-postgresql'

    walkoff_db_secret_name = None
    walkoff_db_hostname = None
    if click.confirm('Do you have an existing PostgreSQL database to store WALKOFF application data in? '
                     '(This can be the same or different as the previous)'):
        walkoff_db_hostname = click.prompt('Enter the database hostname (if it is not in the same Kubernetes namespace '
                                           'as WALKOFF, enter a fully qualified domain name)')
        walkoff_db_username = click.prompt('Enter a username that is able to create/read/write/update databases')
        if click.confirm("Is the PostgreSQL password already stored in a Kubernetes secret?"):
            walkoff_db_secret_name = click.prompt('Available secrets: {}\nEnter the name of the secret the PostgreSQL '
                                                  'password is stored in with a key of "postgres-password" '
                                                  '(leave blank for none): ',
                                                  default="")
            if walkoff_db_secret_name not in existing_secrets:
                walkoff_db_secret_name = None
                click.echo('No secret with that name in this namespace. Creating a new secret to store password.')

    if not walkoff_db_secret_name:
        walkoff_db_secret_name = "walkoff-postgres-secret"
        walkoff_db_username = click.prompt('Enter a username to create', default='walkoff')
        walkoff_db_password = click.prompt('Enter a password for the PostgreSQL instance', hide_input=True,
                                confirmation_prompt=True, default='walkoff')
        walkoff_db_secret_obj = k8s_client.V1Secret(metadata={'name': walkoff_db_secret_name}, data={
            'postgres-password': b64encode(walkoff_db_password.encode('utf-8')).decode('utf-8')})
        try:
            k8s_api.create_namespaced_secret(namespace, walkoff_db_secret_obj)
        except k8s_client.rest.ApiException as e:
            click.echo("Error creating secret:\n{}".format(str(e)))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    with open("k8s_manifests/setupfiles/walkoff-postgres-helm-values.yaml", 'r+') as f:
        try:
            y = yaml.load(f)
            y['postgresqlUsername'] = walkoff_db_username
            y['postgresqlPassword'] = walkoff_db_password
            f.seek(0)
            f.truncate()
            yaml.dump(y, f, default_flow_style=False)
        except yaml.YAMLError as e:
            click.echo("Error reading k8s_manifests/setupfiles/walkoff-postgres-helm-values.yaml")
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    if not walkoff_db_hostname:
        helm_command(['install', 'stable/postgresql',
                      '--name', 'walkoff-db',
                      '--values', 'k8s_manifests/setupfiles/walkoff-postgres-helm-values.yaml'],
                     tiller_namespace)
        walkoff_db_hostname = 'walkoff-db-postgresql'

    walkoff_ca_key_pair = None
    if click.confirm('Do you have an existing CA signing key pair stored in Kubernetes secrets?'):
        walkoff_ca_key_pair = click.prompt(
            'Available secrets: {}\nEnter the name of the secret the key pair is stored in (leave blank for none): ',
            default="")
        if walkoff_ca_key_pair not in existing_secrets:
            walkoff_ca_key_pair = None
            click.echo('No secret with that name in this namespace. Creating a new secret to store keypair.')

    if not walkoff_ca_key_pair:
        crt = None
        key = None
        if click.confirm('Do you have existing CA signing key pair files?'):
            while not crt:
                crt = click.prompt('Enter the path to a cert (.crt) file: ')
                try:
                    with open(crt, 'rb') as f:
                        crt = b64encode(f.read()).decode('ascii')
                        click.echo('Successfully loaded cert')
                except IOError as e:
                    click.echo('Error reading {}: {}'.format(crt, e))
                    crt = None

            while not key:
                key = click.prompt('Enter the path to the matching private key (.key) file: ')
                try:
                    with open(key, 'rb') as f:
                        key = b64encode(f.read()).decode('ascii')
                        click.echo('Successfully loaded key.')
                except IOError as e:
                    click.echo('Error reading {}: {}'.format(key, e))
                    key = None

        if not all((crt, key)):
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            public_key = private_key.public_key()
            builder = x509.CertificateBuilder()
            builder = builder.subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u'walkoff')
            ]))
            builder = builder.issuer_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u'walkoff'),
            ]))
            builder = builder.not_valid_before(datetime.datetime.today() - datetime.timedelta(days=1))
            builder = builder.not_valid_after(datetime.datetime.today() + datetime.timedelta(days=3650))
            builder = builder.serial_number(int(uuid.uuid4()))
            builder = builder.public_key(public_key)

            builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
            builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
                                            critical=False)
            builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            certificate = builder.sign(
                private_key=private_key, algorithm=hashes.SHA256(),
                backend=default_backend()
            )

            with open("ca.key", "wb") as f:
                byte_cert = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                )
                key = b64encode(byte_cert).decode('ascii')
                f.write(byte_cert)

            with open("ca.crt", "wb") as f:
                byte_key = certificate.public_bytes(
                    encoding=serialization.Encoding.PEM,
                )
                crt = b64encode(byte_key).decode('ascii')
                f.write(byte_key)

        tls_secret = k8s_client.V1Secret(metadata={'name': 'walkoff-ca-key-pair'},
                                         data={'tls.crt': crt, 'tls.key': key},
                                         type='kubernetes.io/tls')
        try:
            k8s_api.create_namespaced_secret('default', tls_secret)
        except k8s_client.rest.ApiException as e:
            click.echo("Error creating secret:\n{}".format(str(e)))
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

        walkoff_ca_key_pair = 'walkoff-ca-key-pair'

    helm_command(['install', 'stable/cert-manager',
                  '--name', 'walkoff-cert-manager'],
                 tiller_namespace)

    with open("k8s_manifests/setupfiles/cert-issuer.yaml", 'r+') as f:
        try:
            y = yaml.load(f)
            y['spec']['ca']['secretName'] = walkoff_ca_key_pair
            f.seek(0)
            f.truncate()
            yaml.dump(y, f, default_flow_style=False)
        except yaml.YAMLError as e:
            click.echo("Error reading k8s_manifests/setupfiles/cert-issuer.yaml")
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    kubectl_command(['apply', '-f', 'k8s_manifests/setupfiles/cert-issuer.yaml'],
                    namespace)
    kubectl_command(['apply', '-f', 'k8s_manifests/setupfiles/cert.yaml'],
                    namespace)

    with open("k8s_manifests/setupfiles/walkoff-values.yaml", 'r+') as f:
        try:
            y = yaml.load(f)
            y['namespace'] = namespace
            y['resources']['redis']['service_name'] = redis_hostname
            y['resources']['redis']['secret_name'] = redis_secret_name
            y['resources']['execution_db']['service_name'] = execution_db_hostname
            y['resources']['execution_db']['secret_name'] = execution_secret_name
            y['resources']['execution_db']['username'] = execution_db_username
            y['resources']['walkoff_db']['service_name'] = walkoff_db_hostname
            y['resources']['walkoff_db']['secret_name'] = walkoff_db_secret_name
            y['resources']['walkoff_db']['username'] = walkoff_db_username
            f.seek(0)
            f.truncate()
            yaml.dump(y, f, default_flow_style=False)
        except yaml.YAMLError as e:
            click.echo("Error reading k8s_manifests/setupfiles/walkoff-values.yaml")
            click.echo('You should use the uninstall command to rollback changes made by this installer.')
            return

    helm_command(['install', 'k8s_manifests/helm_charts/walkoff',
                  '--name', 'walkoff-deployment'],
                 tiller_namespace)

    # helm_command(['install', 'stable/docker-registry',
    #               '--name', 'walkoff-docker-registry'],
    #               tiller_namespace)


# https://github.com/helm/charts/blob/master/stable/docker-registry/README.md

@click.command()
@click.pass_context
def uninstall(ctx):
    """
    Removes resources and deployments created by this installer from a Kubernetes cluster.
    """

    if click.confirm(("Are you sure you wish to uninstall WALKOFF from your Kubernetes cluster? "
                      "(This will only uninstall components that WALKOFF created through this installer.)")):
        k8s_api = None
        try:
            config_dir = os.environ.get('KUBECONFIG', os.path.join(os.path.expanduser("~"), ".kube", "config"))
            config_dir = click.prompt("Enter location of kubernetes config",
                                      default=config_dir)

            contexts, current = config.list_kube_config_contexts(config_file=config_dir)
            contexts = [context["name"] for context in contexts]
            current = current["name"]

            context = click.prompt("Available contexts: {}\nEnter context to uninstall WALKOFF from: ".format(contexts),
                                   default=current)

            config.load_kube_config(config_file=config_dir, context=context)
            k8s_api = k8s_client.CoreV1Api()
        except IOError as e:
            print("Could not open config: {}".format(e))

        namespaces = k8s_api.list_namespace()
        namespaces = [ns.metadata.name for ns in namespaces.items]
        namespace = click.prompt(
            "Available namespaces: {}\nEnter namespace to uninstall WALKOFF from".format(namespaces),
            default="default")

        tiller_namespace = click.prompt('Enter the namespace your Tiller service resides in',
                                        default='kube-system')

        kubectl_command(['delete', 'cert', 'walkoff-cert'], namespace, exit_on_err=False)
        kubectl_command(['delete', 'issuer', 'walkoff-ca-issuer'], namespace, exit_on_err=False)
        kubectl_command(['delete', 'secrets',
                         'walkoff-redis-secret',
                         'walkoff-zmq-private-keys', 'walkoff-zmq-public-keys',
                         'walkoff-postgres-execution-secret', 'walkoff-postgres-secret',
                         'walkoff-ca-key-pair'],
                        namespace, exit_on_err=False)

        kubectl_command(['delete', 'pvc',
                         'data-execution-db-postgresql-0',
                         'redis-data-walkoff-redis-master-0',
                         'data-walkoff-db-postgresql-0'],
                        namespace, exit_on_err=False)

        kubectl_command(['delete', 'crd',
                         'certificates.certmanager.k8s.io',
                         'clusterissuers.certmanager.k8s.io',
                         'issuers.certmanager.k8s.io'],
                        namespace, exit_on_err=False)

        helm_command(['del', '--purge', 'walkoff-redis'], tiller_namespace, exit_on_err=False)
        helm_command(['del', '--purge', 'walkoff-db'], tiller_namespace, exit_on_err=False)
        helm_command(['del', '--purge', 'execution-db'], tiller_namespace, exit_on_err=False)
        helm_command(['del', '--purge', 'walkoff-cert-manager'], tiller_namespace, exit_on_err=False)
        helm_command(['del', '--purge', 'walkoff-deployment'], tiller_namespace, exit_on_err=False)
        # helm_command(['del', '--purge', 'walkoff-docker-registry'], tiller_namespace)


def install_helm_online(ctx):
    click.echo('Helm not found. Installing... ')
    response = requests.get('https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get')
    if response.status_code == 200:
        with open('get_helm.sh') as f:
            f.write(response.text)
        os.chmod('get_helm.sh', stat.S_IXUSR)
        subprocess.call(['get_helm.sh'])
    else:
        click.echo("Could not connect to Helm's GitHub to retrieve script")
        ctx.exit(1)
    verify_helm_exists(ctx)


def install_helm_offline(ctx):
    click.echo('Helm not found. Installing...')
    archive = tarfile.open('./helm.tgz')
    archive.extractall('./helm')
    os.rename('./helm/helm', '/usr/local/bin/helm')
    verify_helm_exists(ctx)


def verify_helm_exists(ctx):
    if not find_executable('helm'):
        click.echo('Could not install Helm. '
                   'Please see https://docs.helm.sh/using_helm/#installing-helm for more information.')
        ctx.exit(1)


def setup_helm():
    click.echo('setting up Helm with proper TLS, RBAC')


def add_charts_offline():
    click.echo('Adding charts offline using helm repo add <name> <URL>')


def install_docker_repository():
    pass


def populate_docker_repository():
    pass
