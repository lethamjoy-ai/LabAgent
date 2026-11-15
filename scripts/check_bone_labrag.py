"""One explicit direct query, then offline HTTP replay through the real chat UI.

--live is guarded by a persistent attempt marker to avoid duplicate chargeable
requests after interruption. Without --live this script only replays saved data.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
import httpx
from dotenv import load_dotenv
from streamlit.testing.v1 import AppTest
from app.bootstrap import create_auth
from app.integrations.labrag_client import HttpLabRAGClient, LabRAGError

QUESTION="What specimen and volume are required for Bone Alkaline Phosphatase, Serum?"
ROOT=Path(__file__).resolve().parents[1]
RESPONSE=ROOT/'data'/'bone-labrag-direct-response.json'
ATTEMPT=ROOT/'data'/'bone-labrag-live-attempt.json'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',help='Make the one authorized direct query, then replay it offline.')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    if args.live:
        if RESPONSE.exists() or ATTEMPT.exists():
            raise SystemExit('A live attempt already exists. Run without --live to replay; do not repeat the chargeable query.')
        ATTEMPT.parent.mkdir(parents=True,exist_ok=True)
        with ATTEMPT.open('x',encoding='utf-8') as f:
            json.dump({'question':QUESTION,'query_attempts':1,'endpoint':'http://127.0.0.1:8000/api/v1/query'},f,indent=2)
        try:
            result=HttpLabRAGClient('http://127.0.0.1:8000',float(os.getenv('LABRAG_TIMEOUT_SECONDS','90'))).query(None,QUESTION)
        except LabRAGError as exc:
            print(json.dumps({'query_attempts':1,'code':exc.code,'message':str(exc)}))
            return
        RESPONSE.write_text(json.dumps(result.model_dump(),indent=2),encoding='utf-8')
    if not RESPONSE.exists():
        raise SystemExit('No saved response to replay. No request was sent.')
    body=json.loads(RESPONSE.read_text(encoding='utf-8'))
    replay_calls=[]
    def handler(request):
        question=json.loads(request.content)['question']
        assert question==QUESTION
        replay_calls.append(question)
        return httpx.Response(200,json=body)
    import app.bootstrap
    original_factory=app.bootstrap.build_labrag_client
    app.bootstrap.build_labrag_client=lambda:HttpLabRAGClient('http://offline-replay.test',transport=httpx.MockTransport(handler))
    try:
        with tempfile.TemporaryDirectory(prefix='bone-labrag-replay-') as folder:
            os.environ['LAB_DATABASE']=str(Path(folder)/'synthetic.sqlite3')
            os.environ['LAB_PLANNER']='demo'
            auth=create_auth()
            password=secrets.token_urlsafe(32)
            auth.staff.put_account('REPLAY-STAFF','replay.demo',auth.hasher.hash(password),'Synthetic','Staff','Staff',active=True)
            ui=AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
            ui.text_input[0].set_value('replay.demo')
            ui.text_input[1].set_value(password)
            next(b for b in ui.button if b.label=='Sign in').click().run(timeout=30)
            ui.chat_input[0].set_value(QUESTION).run(timeout=30)
            assert not ui.exception
            state=ui.session_state['conversation']
            tool=state['tool_results'][0]['result']
            assert tool['status']==body['status']
            assert tool['citations']==body['citations']
            assert body['answer'] in '\n'.join(t.value for t in ui.text)
            report={'live_query_attempts':1,'direct_status':body['status'],'request_id':body['request_id'],
                'citation_count':len(body['citations']),'matched_tests':body['matched_tests'],
                'offline_replay_calls':len(replay_calls),'replayed_question_unchanged':replay_calls==[QUESTION],
                'status_and_citations_preserved':True,'answer_reached_ui':True}
            (ROOT/'data'/'bone-labrag-replay-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
    finally:
        app.bootstrap.build_labrag_client=original_factory


if __name__=='__main__':
    main()
