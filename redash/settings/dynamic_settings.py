import os
from collections import defaultdict


# Replace this method with your own implementation in case you want to limit the time limit on certain queries or users.
def query_time_limit(is_scheduled, user_id, org_id):
    from redash import settings

    if is_scheduled:
        return settings.SCHEDULED_QUERY_TIME_LIMIT
    else:
        return settings.ADHOC_QUERY_TIME_LIMIT


def periodic_jobs():
    """Schedule any custom periodic jobs here. For example:

    from time import timedelta
    from somewhere import some_job, some_other_job

    return [
        {"func": some_job, "interval": timedelta(hours=1)},
        {"func": some_other_job, "interval": timedelta(days=1)}
    ]
    """
    pass


# This provides the ability to override the way we store QueryResult's data column.
# Reference implementation: redash.models.DBPersistence
QueryResultPersistence = None


def _private_key_from_env():
    """Load an SSH private key from the ``REDASH_SSH_TUNNEL_PRIVATE_KEY`` environment
    variable, which holds the key contents (PEM/OpenSSH text), not a file path. An
    optional passphrase may be supplied via ``REDASH_SSH_TUNNEL_PRIVATE_KEY_PASSWORD``.

    Loading the key from an environment variable (rather than a path on disk) is
    convenient for containerized deployments such as ECS or Kubernetes, where secrets
    are injected as environment variables and there is no persistent filesystem on which
    to mount a key file.

    Returns a ``paramiko.PKey`` instance, or ``None`` when the variable is unset.
    """
    key_data = os.environ.get("REDASH_SSH_TUNNEL_PRIVATE_KEY")
    if not key_data:
        return None

    import io

    import paramiko

    password = os.environ.get("REDASH_SSH_TUNNEL_PRIVATE_KEY_PASSWORD") or None

    errors = []
    # Try the common key types in turn; the env var doesn't tell us which one it is.
    # paramiko usually raises SSHException for a wrong-type key, but some malformed keys
    # leak other errors (e.g. a corrupted OpenSSH key raises UnicodeDecodeError), so we
    # catch broadly and surface a single clear SSHException if none of the types load.
    for key_class in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_class.from_private_key(io.StringIO(key_data), password=password)
        except Exception as e:
            errors.append("{}: {}".format(key_class.__name__, e))

    raise paramiko.SSHException(
        "Could not load REDASH_SSH_TUNNEL_PRIVATE_KEY as an Ed25519, ECDSA, or RSA private key ({}).".format(
            "; ".join(errors)
        )
    )


def ssh_tunnel_auth():
    """
    To enable data source connections via SSH tunnels, provide your SSH authentication
    pkey here. Return a string pointing at your **private** key's path (which will be used
    to extract the public key), or a `paramiko.pkey.PKey` instance holding your **public** key.

    By default the key is loaded from the ``REDASH_SSH_TUNNEL_PRIVATE_KEY`` environment
    variable (its contents, with an optional ``REDASH_SSH_TUNNEL_PRIVATE_KEY_PASSWORD``
    passphrase) when set. Override this function in your own dynamic settings module to
    load the key some other way (e.g. from a file path).
    """
    private_key = _private_key_from_env()
    if private_key is not None:
        return {"ssh_pkey": private_key}

    return {
        # 'ssh_pkey': 'path_to_private_key', # or instance of `paramiko.pkey.PKey`
        # 'ssh_private_key_password': 'optional_passphrase_of_private_key',
    }


def database_key_definitions(default):
    """
    All primary/foreign keys in Redash are of type `db.Integer` by default.
    You may choose to use different column types for primary/foreign keys. To do so, add an entry below for each model you'd like to modify.
    For each model, add a tuple with the database type as the first item, and a dict including any kwargs for the column definition as the second item.
    """
    definitions = defaultdict(lambda: default)
    definitions.update(
        {
            # "DataSource": (db.String(255), {
            #    "default": generate_key
            # })
        }
    )

    return definitions


# Since you can define custom primary key types using `database_key_definitions`, you may want to load certain extensions when creating the database.
# To do so, simply add the name of the extension you'd like to load to this list.
database_extensions = []
