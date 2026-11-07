"""Copy a real MySQL database into an isolated generated test schema using only SQL."""
from contextlib import contextmanager
from uuid import uuid4
import os
import re
from connector.connector import Connector
from api.store import transaction
from api.forecasting import run_inference

@contextmanager
def cloned_database(prefix='folmare_test_'):
    if prefix not in ('folmare_test_','folmare_ui_'):raise ValueError('Test database prefix required')
    source=Connector();source.connect();original=source.database
    name=prefix+uuid4().hex[:12]
    assert re.fullmatch(r'folmare_(test|ui)_[0-9a-f]{12}',name)
    source.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
    test=Connector(database=name);test.connect();prior=os.environ.get('MYSQL_DATABASE')
    try:
        cursor=source.conn.cursor();cursor.execute('SHOW TABLES');tables=[row[0] for row in cursor.fetchall()]
        test.execute('SET FOREIGN_KEY_CHECKS=0')
        for table in tables:
            cursor.execute(f'SHOW CREATE TABLE `{table}`');test.execute(cursor.fetchone()[1])
            test.execute(f'INSERT INTO `{name}`.`{table}` SELECT * FROM `{original}`.`{table}`')
        test.execute('SET FOREIGN_KEY_CHECKS=1');cursor.close()
        os.environ['MYSQL_DATABASE']=name
        with transaction() as store:run_inference(store)
        yield name
    finally:
        if prior is None:os.environ.pop('MYSQL_DATABASE',None)
        else:os.environ['MYSQL_DATABASE']=prior
        test.close()
        assert name!=original and re.fullmatch(r'folmare_(test|ui)_[0-9a-f]{12}',name)
        source.execute(f'DROP DATABASE `{name}`');source.close()
