"""Explicit manual verification: at most ONE live query, using a disposable staff DB."""
import json
import os
import secrets
import tempfile
from pathlib import Path
from streamlit.testing.v1 import AppTest
from app.bootstrap import create_auth
from app.integrations.labrag_client import HttpLabRAGClient


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--live-query',action='store_true',help='Authorize exactly one BUN API query for this invocation.')
    if not parser.parse_args().live_query:
        parser.error('This manual check requires --live-query; it is not an automated test.')
    calls=[]
    original=HttpLabRAGClient.query
    def limited(self,test_id,question):
        if calls:
            raise RuntimeError('Live-query limit reached; no second request was sent.')
        calls.append({'question':question})
        result=original(self,test_id,question)
        calls[0]['response']=result
        return result
    HttpLabRAGClient.query=limited
    with tempfile.TemporaryDirectory(prefix='labagent-live-ui-') as folder:
        os.environ['LAB_DATABASE']=str(Path(folder)/'synthetic.sqlite3')
        os.environ['LAB_PLANNER']='demo'
        os.environ['LABRAG_BASE_URL']='http://127.0.0.1:8000'
        os.environ['LABRAG_TIMEOUT_SECONDS']='90'
        auth=create_auth()
        password=secrets.token_urlsafe(32)
        auth.staff.put_account('CHECK-STAFF','check.demo',auth.hasher.hash(password),'Synthetic','Staff','Staff',active=True)
        ui=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run(timeout=30)
        ui.text_input[0].set_value('check.demo')
        ui.text_input[1].set_value(password)
        next(b for b in ui.button if b.label=='Sign in').click().run(timeout=30)
        ui.chat_input[0].set_value('What specimen is required for BUN?').run(timeout=120)
        if ui.exception:
            raise RuntimeError('Streamlit UI raised an exception during the single live check.')
        result=calls[0].get('response') if calls else None
        if result is None:
            print(json.dumps({'query_attempts':len(calls),'ui_response':ui.session_state['conversation']['response']}))
            return
        shown='\n'.join(item.value for item in ui.text)
        report={'query_attempts':len(calls),'sent_question':calls[0]['question'],'status':result.status,
            'request_id':result.request_id,'answer_reached_ui':result.answer in shown,
            'citation_count':len(result.citations),'all_citations_reached_ui':all(c.display(i) in shown for i,c in enumerate(result.citations,1)),
            'citations':[c.model_dump() for c in result.citations],
            'matched_tests':[t.model_dump() for t in result.matched_tests]}
        print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
