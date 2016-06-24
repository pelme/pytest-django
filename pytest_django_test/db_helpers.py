import os
import subprocess
import sqlite3

import pytest

from django.conf import settings
from django.db import connection
from django.db import transaction

from .compat import force_text
from .app.models import Item


# Construct names for the "inner" database used in runpytest tests
DB_NAME = settings.DATABASES['default']['NAME']
TEST_DB_NAME = settings.DATABASES['default']['TEST']['NAME'] or 'test_{}'.format(DB_NAME)

if TEST_DB_NAME == ':memory:':
    DB_NAME = '/invalid_should_never_be_accessed'
else:
    DB_NAME += '_inner'
    TEST_DB_NAME += '_inner'


def get_db_engine():
    from django.conf import settings
    return settings.DATABASES['default']['ENGINE'].split('.')[-1]


class CmdResult(object):
    def __init__(self, status_code, std_out, std_err):
        self.status_code = status_code
        self.std_out = std_out
        self.std_err = std_err


def run_cmd(*args):
    r = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdoutdata, stderrdata = r.communicate()
    ret = r.wait()
    return CmdResult(ret, stdoutdata, stderrdata)


def run_mysql(*args):
    from django.conf import settings
    user = settings.DATABASES['default'].get('USER', None)
    if user:
        args = ('-u', user) + tuple(args)
    args = ('mysql',) + tuple(args)
    return run_cmd(*args)


def skip_if_sqlite_in_memory():
    from django.conf import settings

    if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3' \
            and settings.DATABASES['default']['NAME'] == ':memory:':
        pytest.skip('Do not test db reuse since database does not support it')


def drop_database(name=TEST_DB_NAME, suffix=None):
    assert bool(name) ^ bool(suffix), 'name and suffix cannot be used together'

    if suffix:
        name = '%s_%s' % (name, suffix)

    if get_db_engine() == 'postgresql_psycopg2':
        r = run_cmd('psql', 'postgres', '-c', 'DROP DATABASE %s' % name)
        assert ('DROP DATABASE' in force_text(r.std_out) or
                'does not exist' in force_text(r.std_err))
        return

    if get_db_engine() == 'mysql':
        r = run_mysql('-e', 'DROP DATABASE %s' % name)
        assert ('database doesn\'t exist' in force_text(r.std_err) or
                r.status_code == 0)
        return

    if get_db_engine() == 'sqlite3':
        if name == ':memory:':
            raise AssertionError(
                'sqlite in-memory database cannot be dropped!')
        if os.path.exists(name):
            os.unlink(name)
        return

    raise AssertionError('%s cannot be tested properly!' % get_db_engine())


def db_exists(db_suffix=None):
    name = TEST_DB_NAME

    if db_suffix:
        name = '%s_%s' % (name, db_suffix)

    if get_db_engine() == 'postgresql_psycopg2':
        r = run_cmd('psql', name, '-c', 'SELECT 1')
        return r.status_code == 0

    if get_db_engine() == 'mysql':
        r = run_mysql(name, '-e', 'SELECT 1')
        return r.status_code == 0

    if get_db_engine() == 'sqlite3':
        if TEST_DB_NAME == ':memory:':
            raise AssertionError(
                'sqlite in-memory database cannot be checked for existence!')
        return os.path.exists(name)

    raise AssertionError('%s cannot be tested properly!' % get_db_engine())


def mark_database():
    if get_db_engine() == 'postgresql_psycopg2':
        r = run_cmd('psql', TEST_DB_NAME, '-c', 'CREATE TABLE mark_table();')
        assert r.status_code == 0
        return

    if get_db_engine() == 'mysql':
        r = run_mysql(TEST_DB_NAME, '-e', 'CREATE TABLE mark_table(kaka int);')
        assert r.status_code == 0
        return

    if get_db_engine() == 'sqlite3':
        if TEST_DB_NAME == ':memory:':
            raise AssertionError('sqlite in-memory database cannot be marked!')

        conn = sqlite3.connect(TEST_DB_NAME)
        try:
            with conn:
                conn.execute('CREATE TABLE mark_table(kaka int);')
        finally:  # Close the DB even if an error is raised
            conn.close()
        return

    raise AssertionError('%s cannot be tested properly!' % get_db_engine())


def mark_exists():
    if get_db_engine() == 'postgresql_psycopg2':
        r = run_cmd('psql', TEST_DB_NAME, '-c', 'SELECT 1 FROM mark_table')

        # When something pops out on std_out, we are good
        return bool(r.std_out)

    if get_db_engine() == 'mysql':
        r = run_mysql(TEST_DB_NAME, '-e', 'SELECT 1 FROM mark_table')

        return r.status_code == 0

    if get_db_engine() == 'sqlite3':
        if TEST_DB_NAME == ':memory:':
            raise AssertionError(
                'sqlite in-memory database cannot be checked for mark!')

        conn = sqlite3.connect(TEST_DB_NAME)
        try:
            with conn:
                conn.execute('SELECT 1 FROM mark_table')
                return True
        except sqlite3.OperationalError:
            return False
        finally:  # Close the DB even if an error is raised
            conn.close()

    raise AssertionError('%s cannot be tested properly!' % get_db_engine())


def noop_transactions():
    """Test whether transactions are disabled.

    Return True if transactions are disabled, False if they are
    enabled.
    """

    # Newer versions of Django simply run standard tests in an atomic block.
    if hasattr(connection, 'in_atomic_block'):
        return connection.in_atomic_block
    else:
        with transaction.commit_manually():
            Item.objects.create(name='transaction_noop_test')
            transaction.rollback()

        try:
            item = Item.objects.get(name='transaction_noop_test')
        except Item.DoesNotExist:
            return False
        else:
            item.delete()
            return True
