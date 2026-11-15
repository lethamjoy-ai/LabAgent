"""One explicitly authorized live C-Peptide diagnostic query, no retries."""
import json
from pathlib import Path
from app.integrations.labrag_client import HttpLabRAGClient,LabRAGError

QUESTION="How should a patient prepare for C-Peptide, Serum?"
ROOT=Path(__file__).resolve().parents[1]


def main():
    marker=ROOT/'data'/'cpeptide-live-attempt.json'
    marker.parent.mkdir(parents=True,exist_ok=True)
    if marker.exists():
        raise SystemExit('A live attempt is already recorded; no duplicate request was sent.')
    with marker.open('x',encoding='utf-8') as f:
        json.dump({'question':QUESTION,'query_attempts':1},f,indent=2)
    try:
        answer=HttpLabRAGClient('http://127.0.0.1:8000',90).query(None,QUESTION)
        report={'query_attempts':1,'result':answer.model_dump()}
    except LabRAGError as exc:
        report={'query_attempts':1,'code':exc.code,'http_status':exc.http_status,
            'request_id':exc.request_id,'api_error':exc.api_error,'message':str(exc)}
    output=ROOT/'data'/'cpeptide-live-result.json'
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    # Only sanitized metadata, never provider error bodies or credentials.
    if 'result' in report:
        result=report['result']
        print(json.dumps({'query_attempts':1,'status':result['status'],'request_id':result['request_id'],
            'citations':len(result['citations']),'matched_tests':result['matched_tests']},indent=2))
    else:
        print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
