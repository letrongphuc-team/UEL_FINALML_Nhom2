"""Exercise the local inference endpoint and verify persisted rows for the returned run."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from api.app import app
from api.store import transaction

def check():
    client=app.test_client()
    csrf=client.get('/api/session').json['csrf']
    login=client.post('/api/auth/login',json=dict(email='manager@haircare.local',password='DemoAdmin123!',role='admin'),headers={'X-CSRF-Token':csrf})
    assert login.status_code==200,login.json
    result=client.post('/api/admin/ml',json={},headers={'X-CSRF-Token':login.json['csrf']})
    assert result.status_code==200,result.json
    run=result.json['run_id']
    with transaction() as store:
        rows=store.one('SELECT COUNT(*) AS n FROM Inventory_Forecast WHERE run_id=%s',(run,))['n']
        recommendations=store.one('''SELECT COUNT(*) AS n FROM Inventory_Recommendation r JOIN Inventory_Forecast f ON r.forecast_id=f.forecast_id WHERE f.run_id=%s''',(run,))['n']
    assert rows==2655 and recommendations==59,(rows,recommendations)
    report=client.get('/api/admin/ml')
    assert report.json['inference_run_id']==run
    print(f'Packaged inference verified and persisted: {run}; forecast rows={rows}; recommendations={recommendations}')

if __name__=='__main__':check()
