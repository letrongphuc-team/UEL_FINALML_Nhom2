"""Start the FOLMARÉ admin UI with this project's MySQL and model modules."""
from pathlib import Path
import argparse
import os
ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description="UEL Final ML — Nhóm 2")
    parser.add_argument('--port',type=int,default=4174)
    parser.add_argument('--refresh-forecast',action='store_true',help='Recompute fitted forecasts from configured MySQL sales')
    args=parser.parse_args()
    from api.integration import create_app
    app=create_app(ROOT/'UI',ROOT/'runtime')
    if args.refresh_forecast:
        from api.store import transaction
        from api.forecasting import run_inference
        with transaction() as store:result=run_inference(store)
        print('Forecasts refreshed:',result['run_id'])
        return
    print(f'FOLMARÉ Admin: http://127.0.0.1:{args.port}/',flush=True)
    app.run(host='127.0.0.1',port=args.port,debug=False,threaded=True)

if __name__=='__main__':main()
