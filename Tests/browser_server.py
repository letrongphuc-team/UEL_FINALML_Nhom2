"""Start the UI against a generated SQL clone; source database business records stay intact."""
from pathlib import Path
import os
import signal
import sys
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT
sys.path.insert(0,str(ROOT))
from mysql_test_support import cloned_database
from api.integration import create_app

def stop(*args):raise KeyboardInterrupt
if __name__=='__main__':
    signal.signal(signal.SIGTERM,stop)
    try:
        with cloned_database('folmare_ui_') as name:
            print('UI test database:',name,flush=True)
            (ROOT/'Tests/artifacts').mkdir(exist_ok=True)
            (ROOT/'Tests/artifacts/ui-test-database.txt').write_text(name)
            create_app(ROOT/'UI',ROOT/'runtime/ui-test').run(host='127.0.0.1',port=int(os.environ.get('UI_TEST_PORT','4189')),debug=False,threaded=True)
    except KeyboardInterrupt:print('Isolated UI database cleaned up.',flush=True)
